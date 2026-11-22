import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from warehouse.source.validation import Conflict, require_position


SOURCE_SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS source_head (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        branch INTEGER NOT NULL, base INTEGER NOT NULL, through INTEGER NOT NULL
    )''',
    '''CREATE TABLE IF NOT EXISTS source_batches (
        branch INTEGER NOT NULL, number INTEGER NOT NULL UNIQUE, changes TEXT NOT NULL,
        PRIMARY KEY (branch, number)
    )''',
    '''CREATE TABLE IF NOT EXISTS source_receipts (
        branch INTEGER NOT NULL, number INTEGER NOT NULL, request_id TEXT NOT NULL,
        receipt TEXT NOT NULL, PRIMARY KEY (branch, number), UNIQUE (branch, request_id),
        FOREIGN KEY (branch, number) REFERENCES source_batches (branch, number)
    )''',
    '''CREATE TABLE IF NOT EXISTS source_facts (
        day TEXT NOT NULL, station TEXT NOT NULL, observation TEXT NOT NULL,
        fact TEXT NOT NULL, PRIMARY KEY (day, station, observation)
    )''',
)


def json_text(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def connect_store(path, *, existing=False):
    path = Path(path)
    if existing:
        connection = sqlite3.connect(path.resolve().as_uri() + '?mode=rw', uri=True,
                                     timeout=10, isolation_level=None)
    else:
        connection = sqlite3.connect(path, timeout=10, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys = ON')
    return connection


@contextmanager
def atomic(connection, *, write=False):
    connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
    try:
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def initialize_source(source, branch=1):
    require_position(branch, 'branch', minimum=1)
    with atomic(source, write=True):
        for statement in SOURCE_SCHEMA:
            source.execute(statement)
        source.execute('INSERT OR IGNORE INTO source_head VALUES (1, ?, 0, 0)', (branch,))
        assert_source_branch(source, branch)


def assert_source_branch(source, branch):
    row = source.execute('SELECT branch FROM source_head WHERE singleton = 1').fetchone()
    if row is None or row['branch'] != branch:
        raise Conflict('workspace branch differs from its registration')


def open_source(path, branch=1, *, existing=False):
    source = connect_store(path, existing=existing)
    try:
        if existing:
            assert_source_branch(source, branch)
        else:
            initialize_source(source, branch)
    except BaseException:
        source.close()
        raise
    return source
