from contextlib import closing

from warehouse.export.packages import DeliveryPackage, package_fingerprint
from warehouse.export.ranges import select_formal_range
from warehouse.export.registry import find_issue, open_issue_registry, record_pending_issue, seal_issue
from warehouse.export.restore_handoff import (
    find_missing_restore_evidence, portable_restore_basis, read_restore_evidence,
)
from warehouse.recovery.catalog import open_control_catalog
from warehouse.recovery.workspace import open_branch_workspace
from warehouse.source.store import atomic
from warehouse.source.validation import WarehouseError, require_day, require_name, require_position


def issue_delivery(root, issue_id, *, day, branch, after, through, restore_id=None):
    require_name(issue_id, 'issue request')
    require_day(day)
    require_position(branch, 'branch', minimum=1)
    require_position(after, 'start')
    require_position(through, 'end', minimum=after)
    if restore_id is None:
        if through == after:
            raise WarehouseError('ordinary delivery requires a nonempty range')
    else:
        require_name(restore_id, 'restore request')
    specification = {'day': day, 'branch': branch, 'after': after, 'through': through, 'restore_id': restore_id}
    with closing(open_issue_registry(root)) as registry, atomic(registry, write=True):
        request = record_pending_issue(registry, issue_id, specification, [])
        if request['status'] == 'sealed':
            return request
        restore = basis = None
        if restore_id is not None:
            evidence = read_restore_evidence(root, restore_id)
            missing = find_missing_restore_evidence(evidence)
            if missing:
                return record_pending_issue(registry, issue_id, specification, missing)
            basis = portable_restore_basis(evidence, branch, after)
            restore = {'restore_id': restore_id, 'checkpoint_id': basis['checkpoint_id'],
                       'base_branch': basis['boundary']['branch'], 'basis_sha256': package_fingerprint(basis)}
        with closing(open_control_catalog(root)) as control, atomic(control, write=True):
            with open_branch_workspace(root, control, branch, create_initial=True) as source, atomic(source):
                batches = select_formal_range(source, branch, after, through)
        if batches is None:
            return record_pending_issue(registry, issue_id, specification, ['formal_range'])
        declaration = {'issue_id': issue_id, 'day': day, 'branch': branch, 'after': after, 'through': through,
                       'restore': restore, 'batch_hashes': [package_fingerprint(batch) for batch in batches]}
        return seal_issue(registry, issue_id, DeliveryPackage(declaration, tuple(batches), basis))


def read_issue_result(root, issue_id):
    with closing(open_issue_registry(root)) as registry:
        return find_issue(registry, issue_id)
