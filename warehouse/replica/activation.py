from warehouse.replica.progress import record_publication, replace_replica_facts, update_scoped_state
from warehouse.replica.requests import record_intake_success, record_scoped_success
from warehouse.report.archive import save_fixed_report
from warehouse.report.daily import build_daily_report


def commit_publication(replica, request_id, package, candidate):
    declare = package.declaration
    boundary = {'branch': declare['branch'], 'through': declare['through']}
    report_id = f'replica:{request_id}'
    receipt = {'status': 'published', 'request_id': request_id, 'issue_id': declare['issue_id'],
               'receipt_id': f'intake:{request_id}', 'boundary': boundary, 'report_id': report_id}
    report = build_daily_report(candidate.facts, declare['day'], boundary, report_id=report_id,
                                intake_receipt=receipt)
    replace_replica_facts(replica, candidate.facts)
    save_fixed_report(replica, report)
    record_publication(replica, package, report_id)
    record_intake_success(replica, request_id, receipt)
    return receipt


def commit_scoped_publication(replica, request, facts, *, expanded):
    """Commit a station-scoped publication: facts, coverage, boundary, report and receipt together."""
    request_id = request['request_id']
    boundary = {'branch': request['branch'], 'through': request['through']}
    report_id = f'replica:{request_id}'
    receipt = {'status': 'published', 'request_id': request_id, 'receipt_id': f'intake:{request_id}',
               'boundary': boundary, 'report_id': report_id}
    report = build_daily_report(facts, request['day'], boundary, report_id=report_id,
                                intake_receipt=receipt)
    report['covered_stations'] = list(request['stations'])
    replace_replica_facts(replica, facts)
    save_fixed_report(replica, report)
    replica.execute('UPDATE replica_current SET branch = ?, through = ?, report_id = ? WHERE singleton = 1',
                    (boundary['branch'], boundary['through'], report_id))
    if expanded:
        update_scoped_state(replica, request['stations'], True)
    record_scoped_success(replica, request_id, receipt)
    return receipt
