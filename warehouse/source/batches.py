import json

from warehouse.source.facts import apply_fact_changes
from warehouse.source.receipts import build_source_receipt, find_source_receipt, record_source_receipt
from warehouse.source.store import atomic, json_text
from warehouse.source.validation import Conflict, require_name, require_position, validate_batch


FORMAL_SELECT = '''SELECT b.branch, b.number, b.changes, r.receipt
                   FROM source_batches AS b JOIN source_receipts AS r
                   ON b.branch = r.branch AND b.number = r.number'''


def read_source_limits(source):
    return dict(source.execute('SELECT branch, base, through FROM source_head WHERE singleton = 1').fetchone())


def read_source_boundary(source):
    limits = read_source_limits(source)
    return {'branch': limits['branch'], 'through': limits['through']}


def _assemble(rows):
    return [{'branch': row['branch'], 'number': row['number'], 'changes': json.loads(row['changes']),
             'receipt': json.loads(row['receipt'])} for row in rows]


def read_formal_batches(source, branch, after, through):
    rows = source.execute(FORMAL_SELECT + ''' WHERE b.branch = ? AND b.number > ? AND b.number <= ?
                                             ORDER BY b.number''', (branch, after, through))
    return _assemble(rows)


def read_formal_history(source, through):
    return _assemble(source.execute(FORMAL_SELECT + ' WHERE b.number <= ? ORDER BY b.number', (through,)))


def insert_formal_batch(source, batch):
    source.execute('INSERT INTO source_batches VALUES (?, ?, ?)',
                   (batch['branch'], batch['number'], json_text(batch['changes'])))
    apply_fact_changes(source, batch)
    record_source_receipt(source, batch['receipt'])


def submit_batch(source, request_id, changes):
    require_name(request_id, 'source request')
    changes = validate_batch(changes)
    with atomic(source, write=True):
        head = read_source_boundary(source)
        branch = head['branch']
        previous = find_source_receipt(source, request_id, branch)
        if previous is not None:
            number = previous['number']
            old = read_formal_batches(source, branch, number - 1, number)[0]
            if old['changes'] != changes:
                raise Conflict('source request already has a different batch')
            return previous
        number = require_position(head['through'] + 1, 'batch number', minimum=1)
        receipt = build_source_receipt(branch, number, request_id, len(changes))
        insert_formal_batch(source, {'branch': branch, 'number': number, 'changes': changes, 'receipt': receipt})
        source.execute('UPDATE source_head SET through = ? WHERE singleton = 1', (number,))
        return receipt
