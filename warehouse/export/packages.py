import hashlib
import json
from dataclasses import dataclass

from warehouse.source.validation import (
    Conflict, WarehouseError, observation_key, require_day, require_fields, require_name,
    require_position, require_reading, validate_batch,
)


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
