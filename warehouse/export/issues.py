from contextlib import closing

from warehouse.export.packages import (
    ADVANCE, OPENING, SCOPED_MODES, DeliveryPackage, ScopedPackage, package_fingerprint,
)
from warehouse.export.ranges import select_formal_range
from warehouse.export.registry import find_issue, open_issue_registry, record_pending_issue, seal_issue, seal_scoped_issue
from warehouse.export.restore_handoff import (
    find_missing_restore_evidence, portable_restore_basis, read_restore_evidence,
)
from warehouse.recovery.catalog import open_control_catalog
from warehouse.recovery.workspace import open_branch_workspace
from warehouse.source.batches import read_formal_history, read_source_limits
from warehouse.source.facts import project_facts, read_facts
from warehouse.source.store import atomic
from warehouse.source.validation import (
    Conflict, WarehouseError, require_day, require_name, require_position, require_station_set,
)


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


def _advance_pieces(source, day, stations, branch, after, through):
    batches = select_formal_range(source, branch, after, through)
    if batches is None:
        return None
    pieces = {}
    for batch in batches:
        rows = [{'row': position, 'change': change, 'source_receipt': batch['receipt']}
                for position, change in enumerate(batch['changes'], 1)
                if change['day'] == day and change['station'] in stations]
        pieces[str(batch['number'])] = {'rows': rows}
    return pieces


def _opening_pieces(source, day, stations, branch, through):
    limits = read_source_limits(source)
    if limits['branch'] != branch:
        raise Conflict('opening branch differs from the workspace branch')
    if through > limits['through']:
        return None
    history = read_formal_history(source, through)
    if len(history) != through:
        raise Conflict('formal history is incomplete at the opening cutoff')
    facts = read_facts(source) if through == limits['through'] else project_facts([], history)
    return {station: {'station': station,
                      'facts': [fact for fact in facts if fact['day'] == day and fact['station'] == station]}
            for station in stations}


def issue_scoped_delivery(root, issue_id, *, day, branch, stations, after, through, mode):
    require_name(issue_id, 'issue request')
    require_day(day)
    require_position(branch, 'branch', minimum=1)
    stations = require_station_set(stations)
    require_position(after, 'start')
    if mode not in SCOPED_MODES:
        raise WarehouseError('unknown scoped delivery mode')
    require_position(through, 'end', minimum=after + 1 if mode == ADVANCE else after)
    specification = {'day': day, 'branch': branch, 'stations': stations, 'after': after,
                     'through': through, 'mode': mode}
    with closing(open_issue_registry(root)) as registry, atomic(registry, write=True):
        request = record_pending_issue(registry, issue_id, specification, [])
        if request['status'] == 'sealed':
            return request
        with closing(open_control_catalog(root)) as control, atomic(control, write=True):
            with open_branch_workspace(root, control, branch, create_initial=True) as source, atomic(source):
                if mode == ADVANCE:
                    pieces = _advance_pieces(source, day, stations, branch, after, through)
                else:
                    pieces = _opening_pieces(source, day, stations, branch, through)
        if pieces is None:
            return record_pending_issue(registry, issue_id, specification, ['formal_range'])
        declaration = {'issue_id': issue_id, 'day': day, 'branch': branch, 'stations': stations,
                       'after': after, 'through': through, 'mode': mode,
                       'sha256': {key: package_fingerprint(content) for key, content in pieces.items()}}
        return seal_scoped_issue(registry, issue_id, ScopedPackage(declaration, pieces))
