import json

from warehouse.report.archive import initialize_report_archive
from warehouse.replica.requests import initialize_intake_requests
from warehouse.source.store import atomic, connect_store, json_text
from warehouse.source.validation import Conflict, observation_key


REPLICA_SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS replica_current (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1), branch INTEGER NOT NULL,
        through INTEGER NOT NULL, report_id TEXT
    )''',
    '''CREATE TABLE IF NOT EXISTS replica_facts (
        day TEXT NOT NULL, station TEXT NOT NULL, observation TEXT NOT NULL, fact TEXT NOT NULL,
        PRIMARY KEY (day, station, observation)
    )''',
    '''CREATE TABLE IF NOT EXISTS replica_publications (
        report_id TEXT PRIMARY KEY, branch INTEGER NOT NULL, start_after INTEGER NOT NULL,
        through INTEGER NOT NULL, issue_id TEXT NOT NULL, restore_id TEXT
    )''',
    '''CREATE TABLE IF NOT EXISTS replica_applied (
        branch INTEGER NOT NULL, number INTEGER NOT NULL, report_id TEXT NOT NULL,
        PRIMARY KEY (branch, number), FOREIGN KEY (report_id) REFERENCES replica_publications (report_id)
    )''',
)


def open_replica(path):
    replica = connect_store(path)
    try:
        replica.execute('PRAGMA journal_mode = WAL')
        with atomic(replica, write=True):
            for statement in REPLICA_SCHEMA:
                replica.execute(statement)
            initialize_intake_requests(replica)
            initialize_report_archive(replica)
            replica.execute('INSERT OR IGNORE INTO replica_current VALUES (1, 1, 0, NULL)')
    except BaseException:
        replica.close()
        raise
    return replica


def read_progress(replica):
    row = replica.execute('SELECT * FROM replica_current WHERE singleton = 1').fetchone()
    return {'boundary': {'branch': row['branch'], 'through': row['through']}, 'report_id': row['report_id']}


def assert_same_branch_range(progress, declaration):
    current = progress['boundary']
    branch, after = declaration['branch'], declaration['after']
    if branch < current['branch']:
        raise Conflict('delivery is from an earlier branch')
    if branch > current['branch']:
        return False
    if after < current['through']:
        raise Conflict('ordinary delivery starts before the current boundary')
    return after == current['through']


def read_replica_facts(replica, day=None):
    if day is None:
        rows = replica.execute('SELECT fact FROM replica_facts ORDER BY day, station, observation')
    else:
        rows = replica.execute('SELECT fact FROM replica_facts WHERE day = ? ORDER BY station, observation',
                               (day,))
    return [json.loads(row['fact']) for row in rows]


def replace_replica_facts(replica, facts):
    replica.execute('DELETE FROM replica_facts')
    replica.executemany('INSERT INTO replica_facts VALUES (?, ?, ?, ?)',
                        ((*observation_key(fact), json_text(fact)) for fact in facts))


def record_publication(replica, package, report_id):
    declaration = package.declaration
    restore = declaration['restore']
    branch, through = declaration['branch'], declaration['through']
    replica.execute('INSERT INTO replica_publications VALUES (?, ?, ?, ?, ?, ?)',
                    (report_id, branch, declaration['after'], through, declaration['issue_id'],
                     None if restore is None else restore['restore_id']))
    replica.executemany('INSERT INTO replica_applied VALUES (?, ?, ?)',
                        ((batch['branch'], batch['number'], report_id) for batch in package.batches))
    replica.execute('UPDATE replica_current SET branch = ?, through = ?, report_id = ? WHERE singleton = 1',
                    (branch, through, report_id))


def count_publications(replica):
    return replica.execute('SELECT COUNT(*) FROM replica_publications').fetchone()[0]
