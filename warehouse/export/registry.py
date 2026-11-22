import json
from pathlib import Path

from warehouse.export.packages import encode_package, encode_scoped_package
from warehouse.source.store import connect_store, json_text
from warehouse.source.validation import Conflict


def open_issue_registry(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    registry = connect_store(root / 'issues.sqlite3')
    try:
        registry.execute('''CREATE TABLE IF NOT EXISTS issues (
            issue_id TEXT PRIMARY KEY, specification TEXT NOT NULL, missing TEXT NOT NULL,
            sealed BLOB
        )''')
    except BaseException:
        registry.close()
        raise
    return registry


def find_issue(registry, issue_id):
    row = registry.execute('SELECT * FROM issues WHERE issue_id = ?', (issue_id,)).fetchone()
    if row is None:
        return None
    package = row['sealed']
    return {'issue_id': issue_id, 'specification': json.loads(row['specification']),
            'status': 'pending' if package is None else 'sealed', 'missing': json.loads(row['missing']),
            'package': package}


def record_pending_issue(registry, issue_id, specification, missing):
    previous = find_issue(registry, issue_id)
    if previous is not None:
        if previous['specification'] != specification:
            raise Conflict('issue request already has a different declaration')
        if previous['status'] == 'sealed':
            return previous
    registry.execute('''INSERT INTO issues VALUES (?, ?, ?, NULL)
                        ON CONFLICT (issue_id) DO UPDATE SET missing = excluded.missing''',
                     (issue_id, json_text(specification), json_text(missing)))
    return find_issue(registry, issue_id)


def seal_issue(registry, issue_id, package):
    encoded = encode_package(package)
    previous = find_issue(registry, issue_id)
    if previous is None or package.declaration['issue_id'] != issue_id:
        raise Conflict('sealing requires the original issue request')
    if previous['status'] == 'sealed':
        if previous['package'] != encoded:
            raise Conflict('issue material is already sealed')
        return previous
    registry.execute('UPDATE issues SET sealed = ?, missing = ? WHERE issue_id = ?', (encoded, '[]', issue_id))
    return find_issue(registry, issue_id)


def seal_scoped_issue(registry, issue_id, package):
    encoded = encode_scoped_package(package)
    previous = find_issue(registry, issue_id)
    if previous is None or package.declaration['issue_id'] != issue_id:
        raise Conflict('sealing requires the original issue request')
    if previous['status'] == 'sealed':
        if previous['package'] != encoded:
            raise Conflict('issue material is already sealed')
        return previous
    registry.execute('UPDATE issues SET sealed = ?, missing = ? WHERE issue_id = ?', (encoded, '[]', issue_id))
    return find_issue(registry, issue_id)
