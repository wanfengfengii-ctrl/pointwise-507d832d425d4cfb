import json

from warehouse.source.store import json_text


def build_source_receipt(branch, number, request_id, row_count):
    return {'receipt_id': f'source:{branch}:{number}', 'branch': branch, 'number': number,
            'request_id': request_id, 'row_count': row_count}


def find_source_receipt(source, request_id, branch=None):
    if branch is None:
        branch = source.execute('SELECT branch FROM source_head WHERE singleton = 1').fetchone()['branch']
    row = source.execute('SELECT receipt FROM source_receipts WHERE branch = ? AND request_id = ?',
                         (branch, request_id)).fetchone()
    return None if row is None else json.loads(row['receipt'])


def read_batch_receipt(source, branch, number):
    row = source.execute('SELECT receipt FROM source_receipts WHERE branch = ? AND number = ?',
                         (branch, number)).fetchone()
    return None if row is None else json.loads(row['receipt'])


def record_source_receipt(source, receipt):
    source.execute('INSERT INTO source_receipts VALUES (?, ?, ?, ?)',
                   (receipt['branch'], receipt['number'], receipt['request_id'], json_text(receipt)))
