from warehouse.export.packages import decode_package
from warehouse.export.ranges import check_range_coverage
from warehouse.replica.activation import commit_publication
from warehouse.replica.materialize import apply_formal_range
from warehouse.replica.progress import assert_same_branch_range, read_progress, read_replica_facts
from warehouse.replica.requests import read_intake_request, record_pending_intake
from warehouse.source.store import atomic
from warehouse.source.validation import Conflict, require_name


def _missing_material(package, progress):
    declaration = package.declaration
    branch = declaration['branch']
    missing = []
    if declaration['restore'] is None:
        if not assert_same_branch_range(progress, declaration):
            missing.append('preceding_publication')
    else:
        if branch <= progress['boundary']['branch']:
            raise Conflict('restore branch is already current or has been superseded')
        if package.basis is None:
            missing.append('restore_basis')
    if not check_range_coverage(branch, declaration['after'], declaration['through'], package.batches):
        missing.append('formal_range')
    return missing


def receive_delivery(replica, request_id, encoded, *, on_stage=None):
    require_name(request_id, 'intake request')
    incoming = decode_package(encoded)
    with atomic(replica, write=True):
        request = record_pending_intake(replica, request_id, incoming)
        if request['receipt'] is not None:
            return request['receipt']
        _missing_material(request['package'], read_progress(replica))

    with atomic(replica, write=True):
        request = read_intake_request(replica, request_id)
        if request['receipt'] is not None:
            return request['receipt']
        package = request['package']
        progress = read_progress(replica)
        missing = _missing_material(package, progress)
        if missing:
            return {'status': 'pending', 'request_id': request_id, 'missing': missing,
                    'boundary': progress['boundary']}
        initial = read_replica_facts(replica) if package.basis is None else package.basis['facts']
        candidate = apply_formal_range(initial, package.batches)
        receipt = commit_publication(replica, request_id, package, candidate)
        if on_stage is not None:
            on_stage('before_commit')
    if on_stage is not None:
        on_stage('after_commit')
    return receipt


def read_receipt(replica, request_id):
    request = read_intake_request(replica, request_id)
    return None if request is None else request['receipt']
