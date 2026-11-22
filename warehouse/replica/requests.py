import json

from warehouse.export.packages import DeliveryPackage, decode_package, encode_package
from warehouse.source.store import json_text
from warehouse.source.validation import Conflict


def initialize_intake_requests(replica):
    replica.execute('''CREATE TABLE IF NOT EXISTS replica_requests (
        request_id TEXT PRIMARY KEY, material BLOB NOT NULL, result TEXT
    )''')


def read_intake_request(replica, request_id):
    row = replica.execute('SELECT material, result FROM replica_requests WHERE request_id = ?',
                          (request_id,)).fetchone()
    if row is None:
        return None
    return {'package': decode_package(row['material']),
            'receipt': None if row['result'] is None else json.loads(row['result'])}


def _merge_material(current, incoming):
    if current.declaration != incoming.declaration:
        raise Conflict('intake request already has a different declaration')
    if current.basis is not None and incoming.basis is not None and current.basis != incoming.basis:
        raise Conflict('intake request already has different checkpoint material')
    batches = {batch['number']: batch for batch in current.batches}
    for batch in incoming.batches:
        number = batch['number']
        if number in batches and batches[number] != batch:
            raise Conflict(f'intake request already has different material for batch {number}')
        batches[number] = batch
    basis = current.basis if current.basis is not None else incoming.basis
    return DeliveryPackage(current.declaration, tuple(batches[n] for n in sorted(batches)), basis)


def record_pending_intake(replica, request_id, package):
    previous = read_intake_request(replica, request_id)
    if previous is not None:
        package = _merge_material(previous['package'], package)
        if previous['receipt'] is not None:
            return previous
    replica.execute('''INSERT INTO replica_requests VALUES (?, ?, NULL)
                       ON CONFLICT (request_id) DO UPDATE SET material = excluded.material''',
                    (request_id, encode_package(package)))
    return {'package': package, 'receipt': None}


def record_intake_success(replica, request_id, receipt):
    updated = replica.execute('UPDATE replica_requests SET result = ? WHERE request_id = ? AND result IS NULL',
                              (json_text(receipt), request_id))
    if updated.rowcount != 1:
        raise Conflict('publication requires one pending intake request')


def initialize_scoped_requests(replica):
    replica.execute('''CREATE TABLE IF NOT EXISTS scoped_requests (
        request_id TEXT PRIMARY KEY, day TEXT NOT NULL, branch INTEGER NOT NULL,
        stations TEXT NOT NULL, through INTEGER NOT NULL, declarations TEXT NOT NULL,
        pieces TEXT NOT NULL, result TEXT
    )''')


def read_scoped_request(replica, request_id):
    row = replica.execute('SELECT * FROM scoped_requests WHERE request_id = ?', (request_id,)).fetchone()
    if row is None:
        return None
    return {'request_id': row['request_id'], 'day': row['day'], 'branch': row['branch'],
            'stations': json.loads(row['stations']), 'through': row['through'],
            'declarations': json.loads(row['declarations']), 'pieces': json.loads(row['pieces']),
            'result': None if row['result'] is None else json.loads(row['result'])}


def record_scoped_request(replica, request_id, day, branch, stations, through):
    replica.execute('INSERT INTO scoped_requests VALUES (?, ?, ?, ?, ?, ?, ?, NULL)',
                    (request_id, day, branch, json_text(stations), through, '{}', '{}'))


def update_scoped_material(replica, request_id, declarations, pieces):
    replica.execute('UPDATE scoped_requests SET declarations = ?, pieces = ? WHERE request_id = ?',
                    (json_text(declarations), json_text(pieces), request_id))


def record_scoped_success(replica, request_id, receipt):
    updated = replica.execute('UPDATE scoped_requests SET result = ? WHERE request_id = ? AND result IS NULL',
                              (json_text(receipt), request_id))
    if updated.rowcount != 1:
        raise Conflict('publication requires one pending scoped request')
