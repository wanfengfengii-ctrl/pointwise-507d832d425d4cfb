from warehouse.replica.progress import record_publication, replace_replica_facts
from warehouse.replica.requests import record_intake_success
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
