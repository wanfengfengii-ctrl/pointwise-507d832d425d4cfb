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
