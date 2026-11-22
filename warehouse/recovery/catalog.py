import json
from pathlib import Path

from warehouse.source.store import atomic, connect_store, json_text
from warehouse.source.validation import Conflict, NotFound


CATALOG_SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS active_workspace (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1), branch INTEGER NOT NULL
    )''',
    '''CREATE TABLE IF NOT EXISTS checkpoints (
        checkpoint_id TEXT PRIMARY KEY, boundary TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('preparing', 'formal')), material TEXT,
        CHECK (state = 'preparing' OR material IS NOT NULL)
    )''',
    '''CREATE TABLE IF NOT EXISTS restores (
        restore_id TEXT PRIMARY KEY, branch INTEGER NOT NULL UNIQUE,
        checkpoint_id TEXT NOT NULL REFERENCES checkpoints (checkpoint_id), result TEXT NOT NULL
    )''',
)


def open_control_catalog(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    control = connect_store(root / 'control.sqlite3')
    try:
        with atomic(control, write=True):
            for statement in CATALOG_SCHEMA:
                control.execute(statement)
            control.execute('INSERT OR IGNORE INTO active_workspace VALUES (1, 1)')
    except BaseException:
        control.close()
        raise
    return control


def active_branch(control):
    return control.execute('SELECT branch FROM active_workspace WHERE singleton = 1').fetchone()['branch']


def read_checkpoint_state(control, checkpoint_id):
    row = control.execute('SELECT * FROM checkpoints WHERE checkpoint_id = ?', (checkpoint_id,)).fetchone()
    if row is None:
        return None
    return {'checkpoint_id': row['checkpoint_id'], 'boundary': json.loads(row['boundary']),
            'status': row['state'], 'material': row['material']}


def record_checkpoint_state(control, checkpoint_id, boundary, material=None, *, formal=False):
    previous = read_checkpoint_state(control, checkpoint_id)
    payload = None if material is None else json_text(material)
    if previous is not None:
        if previous['boundary'] != boundary:
            raise Conflict('checkpoint request already has a different boundary')
        if payload is not None and previous['material'] not in (None, payload):
            raise Conflict('checkpoint material already frozen')
        if previous['status'] == 'formal':
            return previous
        if payload is None:
            payload = previous['material']
    if formal and payload is None:
        raise Conflict('a formal checkpoint requires complete material')
    state = 'formal' if formal else 'preparing'
    control.execute('''INSERT INTO checkpoints VALUES (?, ?, ?, ?)
                       ON CONFLICT (checkpoint_id) DO UPDATE SET state = excluded.state,
                       material = excluded.material''', (checkpoint_id, json_text(boundary), state, payload))
    return read_checkpoint_state(control, checkpoint_id)


def find_formal_checkpoint(control, checkpoint_id):
    record = read_checkpoint_state(control, checkpoint_id)
    return record if record and record['status'] == 'formal' else None


def find_restore_record(control, restore_id):
    row = control.execute('SELECT result FROM restores WHERE restore_id = ?', (restore_id,)).fetchone()
    return None if row is None else json.loads(row['result'])


def find_branch_record(control, branch):
    row = control.execute('SELECT result FROM restores WHERE branch = ?', (branch,)).fetchone()
    return None if row is None else json.loads(row['result'])


def record_restore_result(control, result):
    if find_formal_checkpoint(control, result['checkpoint_id']) is None:
        raise NotFound('formal checkpoint unavailable')
    previous_branch = active_branch(control)
    if result['previous_branch'] != previous_branch or result['branch'] != previous_branch + 1:
        raise Conflict('restore is no longer next for this workspace')
    control.execute('INSERT INTO restores VALUES (?, ?, ?, ?)',
                    (result['restore_id'], result['branch'], result['checkpoint_id'], json_text(result)))
    control.execute('UPDATE active_workspace SET branch = ? WHERE singleton = 1', (result['branch'],))
