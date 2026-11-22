import hashlib
import json
from dataclasses import dataclass

from warehouse.source.validation import (
    Conflict, WarehouseError, observation_key, require_day, require_fields, require_name,
    require_position, require_reading, require_station_set, validate_batch,
)


SCOPED_PACKAGE_VERSION = 2
ADVANCE = 'advance'
OPENING = 'opening'
SCOPED_MODES = (ADVANCE, OPENING)


@dataclass(frozen=True)
class ScopedPackage:
    """Version 2 station-scoped delivery: a trusted declaration plus a subset of its pieces.

    The declaration carries a sha256 mapping from material key to content digest.
    `advance` keys are decimal formal batch numbers and each content is
    {"rows": [{"row", "change", "source_receipt"}, ...]} ordered by formal row;
    an empty rows array certifies that the batch holds no authorized rows for the
    declared day and stations.  `opening` keys are station names and each content is
    {"station": name, "facts": [...]} listing every observation of that station at the
    declared cutoff (empty when the station has none), so a split delivery only ever
    drops keys from `pieces` while the declaration travels unchanged.
    """
    declaration: dict
    pieces: dict

    def to_dict(self):
        return {'version': SCOPED_PACKAGE_VERSION, 'declaration': self.declaration, 'pieces': self.pieces}


@dataclass(frozen=True)
class DeliveryPackage:
    declaration: dict
    batches: tuple = ()
    basis: dict | None = None

    def to_dict(self):
        return {'version': 1, 'declaration': self.declaration, 'batches': self.batches, 'basis': self.basis}


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def encode_package(package):
    return _encoded(package.to_dict())


def package_fingerprint(material):
    data = encode_package(material) if isinstance(material, DeliveryPackage) else _encoded(material)
    return hashlib.sha256(data).hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise WarehouseError(f'duplicate package field {key!r}')
        result[key] = value
    return result


def _require_digest(value):
    if not isinstance(value, str) or len(value) != 64 or set(value) - set('0123456789abcdef'):
        raise WarehouseError('package declaration requires a SHA-256 digest')


def _validate_declaration(declaration):
    require_fields(declaration, ('issue_id', 'day', 'branch', 'after', 'through', 'restore', 'batch_hashes'),
                   'delivery declaration')
    require_name(declaration['issue_id'], 'issue request')
    require_day(declaration['day'])
    branch = require_position(declaration['branch'], 'branch', minimum=1)
    after = require_position(declaration['after'], 'start position')
    through = require_position(declaration['through'], 'end position', minimum=after)
    hashes = declaration['batch_hashes']
    if not isinstance(hashes, list) or len(hashes) != through - after:
        raise WarehouseError('batch digest list does not cover the declared range')
    for digest in hashes:
        _require_digest(digest)
    restore = declaration['restore']
    if restore is None:
        if through == after:
            raise WarehouseError('ordinary delivery requires a nonempty range')
        return
    require_fields(restore, ('restore_id', 'checkpoint_id', 'base_branch', 'basis_sha256'), 'restore declaration')
    require_name(restore['restore_id'], 'restore request')
    require_name(restore['checkpoint_id'], 'checkpoint')
    base_branch = require_position(restore['base_branch'], 'base branch', minimum=1)
    if base_branch >= branch:
        raise Conflict('restored branch must follow its checkpoint branch')
    _require_digest(restore['basis_sha256'])


def _validate_source_receipt(receipt, branch, number, *, row_count=None):
    require_fields(receipt, ('receipt_id', 'branch', 'number', 'request_id', 'row_count'), 'source receipt')
    require_name(receipt['request_id'], 'source request')
    count = require_position(receipt['row_count'], 'receipt row count', minimum=1)
    receipt_branch = require_position(receipt['branch'], 'receipt branch', minimum=1)
    receipt_number = require_position(receipt['number'], 'receipt number', minimum=1)
    if (receipt_branch, receipt_number, receipt['receipt_id']) != (branch, number, f'source:{branch}:{number}'):
        raise Conflict('source receipt does not identify its formal batch')
    if row_count is not None and count != row_count:
        raise Conflict('source receipt row count differs from its batch')
    return count


def _validate_batch_parts(batches, declaration):
    if not isinstance(batches, list):
        raise WarehouseError('package batches must be an ordered array')
    last = declaration['after']
    for batch in batches:
        require_fields(batch, ('branch', 'number', 'changes', 'receipt'), 'formal batch')
        branch = require_position(batch['branch'], 'batch branch', minimum=1)
        number = require_position(batch['number'], 'batch number', minimum=1)
        if branch != declaration['branch'] or not last < number <= declaration['through']:
            raise Conflict('batch does not fit the declared branch, range or order')
        changes = validate_batch(batch['changes'])
        _validate_source_receipt(batch['receipt'], branch, number, row_count=len(changes))
        slot = number - declaration['after'] - 1
        if package_fingerprint(batch) != declaration['batch_hashes'][slot]:
            raise Conflict(f'batch content does not match its declaration at {number}')
        last = number


def _validate_fact(fact, boundary):
    require_fields(fact, ('day', 'station', 'key', 'before', 'liters', 'provenance'), 'checkpoint fact')
    require_day(fact['day'])
    require_name(fact['station'], 'station')
    require_name(fact['key'], 'observation key')
    require_reading(fact['before'], 'before', allow_absent=True)
    require_reading(fact['liters'], 'liters')
    origin = require_fields(fact['provenance'], ('branch', 'batch', 'row', 'source_receipt'), 'row origin')
    branch = require_position(origin['branch'], 'origin branch', minimum=1)
    number = require_position(origin['batch'], 'origin batch', minimum=1)
    row = require_position(origin['row'], 'origin row', minimum=1)
    count = _validate_source_receipt(origin['source_receipt'], branch, number)
    if branch > boundary['branch'] or number > boundary['through'] or row > count:
        raise Conflict('fact provenance is outside its checkpoint')


def _validate_basis(basis, declaration):
    if basis is None:
        return
    restore = declaration['restore']
    if restore is None:
        raise Conflict('an ordinary delivery cannot carry a restore basis')
    require_fields(basis, ('checkpoint_id', 'boundary', 'facts'), 'restore basis')
    boundary = require_fields(basis['boundary'], ('branch', 'through'), 'checkpoint boundary')
    branch = require_position(boundary['branch'], 'checkpoint branch', minimum=1)
    through = require_position(boundary['through'], 'checkpoint position')
    if (basis['checkpoint_id'], branch, through) != (restore['checkpoint_id'], restore['base_branch'],
                                                      declaration['after']):
        raise Conflict('restore basis is for a different checkpoint')
    facts = basis['facts']
    if not isinstance(facts, list):
        raise WarehouseError('checkpoint facts must be an ordered array')
    last = None
    for fact in facts:
        _validate_fact(fact, boundary)
        key = observation_key(fact)
        if last is not None and key <= last:
            raise Conflict('checkpoint facts contain a duplicate or a disordered key')
        last = key
    if package_fingerprint(basis) != restore['basis_sha256']:
        raise Conflict('restore basis differs from the sealed checkpoint material')


def decode_package(encoded):
    try:
        payload = json.loads(encoded, object_pairs_hook=_unique_object)
    except (TypeError, UnicodeError, json.JSONDecodeError) as exc:
        raise WarehouseError('invalid delivery JSON') from exc
    require_fields(payload, ('version', 'declaration', 'batches', 'basis'), 'delivery package')
    if type(payload['version']) is not int or payload['version'] != 1:
        raise WarehouseError('unsupported delivery package version')
    declaration = payload['declaration']
    _validate_declaration(declaration)
    _validate_batch_parts(payload['batches'], declaration)
    _validate_basis(payload['basis'], declaration)
    return DeliveryPackage(declaration, tuple(payload['batches']), payload['basis'])


def encode_scoped_package(package):
    return json.dumps(package.to_dict(), sort_keys=True, separators=(',', ':'), allow_nan=False,
                      ensure_ascii=False).encode('utf-8')


def _validate_scoped_declaration(declaration):
    require_fields(declaration, ('issue_id', 'day', 'branch', 'stations', 'after', 'through', 'mode', 'sha256'),
                   'scoped declaration')
    require_name(declaration['issue_id'], 'issue request')
    require_day(declaration['day'])
    require_position(declaration['branch'], 'branch', minimum=1)
    stations = require_station_set(declaration['stations'])
    after = require_position(declaration['after'], 'start position')
    mode = declaration['mode']
    if mode not in SCOPED_MODES:
        raise WarehouseError('unknown scoped delivery mode')
    through = require_position(declaration['through'], 'end position',
                               minimum=after + 1 if mode == ADVANCE else after)
    digests = declaration['sha256']
    if not isinstance(digests, dict):
        raise WarehouseError('scoped declaration digests require a mapping')
    for key, digest in digests.items():
        if not isinstance(key, str) or not key:
            raise WarehouseError('scoped material keys require nonempty names')
        _require_digest(digest)
    expected = {str(number) for number in range(after + 1, through + 1)} if mode == ADVANCE else set(stations)
    if set(digests) != expected:
        raise WarehouseError('scoped declaration digests do not cover the declared materials')
    declaration['stations'] = stations
    return declaration


def _validate_advance_piece(key, content, declaration):
    require_fields(content, ('rows',), 'advance material')
    rows = content['rows']
    if not isinstance(rows, list):
        raise WarehouseError('advance rows require an ordered array')
    number = int(key)
    last = 0
    for entry in rows:
        require_fields(entry, ('row', 'change', 'source_receipt'), 'advance row')
        row = require_position(entry['row'], 'formal row', minimum=1)
        if row <= last:
            raise Conflict('advance rows contain a duplicate or a disordered row')
        last = row
        change = validate_batch([entry['change']])[0]
        if change['day'] != declaration['day'] or change['station'] not in declaration['stations']:
            raise Conflict('advance row is outside its declared day or stations')
        if row > _validate_source_receipt(entry['source_receipt'], declaration['branch'], number):
            raise Conflict('advance row is outside its formal batch')


def _validate_opening_piece(key, content, declaration):
    require_fields(content, ('station', 'facts'), 'opening material')
    if content['station'] != key or key not in declaration['stations']:
        raise Conflict('opening material does not match its declared station')
    facts = content['facts']
    if not isinstance(facts, list):
        raise WarehouseError('opening facts require an ordered array')
    boundary = {'branch': declaration['branch'], 'through': declaration['through']}
    last = None
    for fact in facts:
        _validate_fact(fact, boundary)
        if fact['day'] != declaration['day'] or fact['station'] != key:
            raise Conflict('opening fact is outside its declared day or station')
        current = observation_key(fact)
        if last is not None and current <= last:
            raise Conflict('opening facts contain a duplicate or a disordered key')
        last = current


def decode_scoped_package(encoded):
    try:
        payload = json.loads(encoded, object_pairs_hook=_unique_object)
    except (TypeError, UnicodeError, json.JSONDecodeError) as exc:
        raise WarehouseError('invalid scoped delivery JSON') from exc
    require_fields(payload, ('version', 'declaration', 'pieces'), 'scoped delivery package')
    if type(payload['version']) is not int or payload['version'] != SCOPED_PACKAGE_VERSION:
        raise WarehouseError('unsupported scoped delivery package version')
    declaration = _validate_scoped_declaration(payload['declaration'])
    pieces = payload['pieces']
    if not isinstance(pieces, dict):
        raise WarehouseError('scoped pieces require a mapping')
    digests = declaration['sha256']
    for key, content in pieces.items():
        if key not in digests:
            raise Conflict('scoped material is not bound by its declaration')
        if package_fingerprint(content) != digests[key]:
            raise Conflict('scoped material does not match its declared digest')
        if declaration['mode'] == ADVANCE:
            _validate_advance_piece(key, content, declaration)
        else:
            _validate_opening_piece(key, content, declaration)
    return ScopedPackage(declaration, pieces)
