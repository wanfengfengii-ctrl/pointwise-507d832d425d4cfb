import sqlite3
from contextlib import closing

from warehouse.recovery.workspace import open_active_workspace
from warehouse.replica.progress import read_progress, read_replica_facts, read_scoped_state
from warehouse.report.archive import open_source_archive, read_fixed_report, save_fixed_report
from warehouse.report.daily import build_daily_report
from warehouse.source.batches import read_source_boundary
from warehouse.source.facts import read_facts
from warehouse.source.receipts import read_batch_receipt
from warehouse.source.store import atomic, json_text
from warehouse.source.validation import Conflict, require_day


def _lookup_source(root, day, freeze):
    with open_active_workspace(root) as source, atomic(source):
        boundary = read_source_boundary(source)
        facts = read_facts(source, day)
        receipts = {}
        for fact in facts:
            origin = fact['provenance']
            key = (origin['branch'], origin['batch'])
            if key not in receipts:
                receipts[key] = read_batch_receipt(source, *key)
            if receipts[key] != origin['source_receipt']:
                raise Conflict('current source fact lacks its formal receipt')
        report_id = f"source:{boundary['branch']}:{boundary['through']}:{day}" if freeze else None
        report = build_daily_report(facts, day, boundary, report_id=report_id)
    if freeze:
        with closing(open_source_archive(root)) as archive, atomic(archive, write=True):
            save_fixed_report(archive, report)
    return report


def _lookup_replica(replica, day, freeze):
    with atomic(replica, write=freeze):
        progress = read_progress(replica)
        publication = progress['report_id']
        published = None if publication is None else read_fixed_report(replica, publication)
        if published is not None and published['day'] == day:
            return published
        report_id = 'replica-day:' + json_text([publication, day]) if freeze else None
        report = build_daily_report(read_replica_facts(replica, day), day, progress['boundary'],
                                    report_id=report_id,
                                    intake_receipt=None if published is None else published['intake_receipt'])
        scoped = read_scoped_state(replica)
        if scoped is not None:
            report['covered_stations'] = list(scoped['stations'])
        if freeze:
            save_fixed_report(replica, report)
        return report


def lookup_daily(store, day, *, freeze=False):
    require_day(day)
    if isinstance(store, sqlite3.Connection):
        return _lookup_replica(store, day, freeze)
    return _lookup_source(store, day, freeze)


def lookup_fixed_report(store, report_id):
    if isinstance(store, sqlite3.Connection):
        return read_fixed_report(store, report_id)
    with closing(open_source_archive(store, existing=True)) as archive:
        return read_fixed_report(archive, report_id)
