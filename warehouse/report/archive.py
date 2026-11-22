import json
from pathlib import Path

from warehouse.report.daily import DailyReport
from warehouse.source.store import connect_store, json_text
from warehouse.source.validation import Conflict, NotFound, require_name


def initialize_report_archive(store):
    store.execute('''CREATE TABLE IF NOT EXISTS fixed_reports (
        report_id TEXT PRIMARY KEY, report TEXT NOT NULL
    )''')


def open_source_archive(root, *, existing=False):
    path = Path(root) / 'source-reports.sqlite3'
    if existing and not path.is_file():
        raise NotFound('source report archive is empty')
    store = connect_store(path, existing=existing)
    try:
        if not existing:
            initialize_report_archive(store)
    except BaseException:
        store.close()
        raise
    return store


def save_fixed_report(store, report: DailyReport):
    report_id = require_name(report['report_id'], 'fixed report')
    payload = json_text(report)
    row = store.execute('SELECT report FROM fixed_reports WHERE report_id = ?', (report_id,)).fetchone()
    if row is not None:
        if row['report'] != payload:
            raise Conflict('report identifier already has different sealed content')
        return
    store.execute('INSERT INTO fixed_reports VALUES (?, ?)', (report_id, payload))


def read_fixed_report(store, report_id) -> DailyReport:
    require_name(report_id, 'fixed report')
    row = store.execute('SELECT report FROM fixed_reports WHERE report_id = ?', (report_id,)).fetchone()
    if row is None:
        raise NotFound(f'unknown fixed report {report_id!r}')
    return json.loads(row['report'])
