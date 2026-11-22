from warehouse.export.packages import ADVANCE, OPENING, decode_package, decode_scoped_package
from warehouse.export.ranges import check_range_coverage
from warehouse.replica.activation import commit_publication, commit_scoped_publication
from warehouse.replica.materialize import apply_formal_range, apply_scoped_rows
from warehouse.replica.progress import (
    assert_same_branch_range, begin_scoped_state, count_publications, read_progress,
    read_replica_facts, read_scoped_state,
)
from warehouse.replica.requests import (
    read_intake_request, read_scoped_request, record_pending_intake, record_scoped_request,
    update_scoped_material,
)
from warehouse.source.store import atomic
from warehouse.source.validation import (
    Conflict, observation_key, require_day, require_name, require_position, require_station_set,
)


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
        if read_scoped_state(replica) is not None:
            raise Conflict('replica follows station-scoped handoff')
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
    if request is not None:
        return request['receipt']
    scoped = read_scoped_request(replica, request_id)
    return None if scoped is None else scoped['result']


def _check_scoped_freshness(stations, through, state, cutoff):
    current = set(state['stations'])
    if not current <= set(stations):
        raise Conflict('scoped station set shrinks the covered stations')
    if set(stations) == current:
        if through <= cutoff:
            raise Conflict('scoped advance does not progress beyond the current cutoff')
    else:
        if state['expanded']:
            raise Conflict('scoped replica already expanded its stations once')
        if through < cutoff:
            raise Conflict('scoped expansion predates the current cutoff')


def _scoped_role(declaration, request, state, cutoff):
    if declaration['day'] != request['day'] or declaration['branch'] != request['branch']:
        raise Conflict('scoped material is for a different day or branch')
    if declaration['mode'] == ADVANCE:
        if declaration['stations'] != state['stations']:
            raise Conflict('advance station set differs from the covered stations')
        if declaration['after'] > cutoff or declaration['through'] < request['through']:
            raise Conflict('advance range does not cover the needed tail')
        return ADVANCE
    new = sorted(set(request['stations']) - set(state['stations']))
    if not new:
        raise Conflict('opening delivery without a station expansion')
    if declaration['stations'] != new:
        raise Conflict('opening station set is not exactly the new stations')
    if declaration['through'] != request['through']:
        raise Conflict('opening cutoff differs from the request cutoff')
    if declaration['after'] > cutoff:
        raise Conflict('opening basis is not yet usable at the current cutoff')
    return OPENING


def _merge_scoped_parts(request, packages, state, cutoff):
    declarations = dict(request['declarations'])
    pieces = {issue: dict(material) for issue, material in request['pieces'].items()}
    roles = {declaration['mode']: issue for issue, declaration in declarations.items()}
    settled = request['result'] is not None
    for package in packages:
        declaration = package.declaration
        issue = declaration['issue_id']
        if issue in declarations:
            if declarations[issue] != declaration:
                raise Conflict('scoped request already has a different declaration for this issue')
        else:
            if settled:
                raise Conflict('scoped request already settled with different material')
            role = _scoped_role(declaration, request, state, cutoff)
            if role in roles:
                raise Conflict(f'scoped request already has a {role} delivery')
            roles[role] = issue
            declarations[issue] = declaration
            pieces.setdefault(issue, {})
        material = pieces[issue]
        for key, content in package.pieces.items():
            if key in material and material[key] != content:
                raise Conflict(f'scoped request already has different material for {key!r}')
            material[key] = content
    return declarations, pieces


def _scoped_missing(request, state, cutoff):
    target = request['through']
    declarations = request['declarations']
    pieces = request['pieces']
    advance = next((item for item in declarations.values() if item['mode'] == ADVANCE), None)
    opening = next((item for item in declarations.values() if item['mode'] == OPENING), None)
    expansion = request['stations'] != state['stations']
    missing = []
    if target > cutoff:
        if advance is None:
            missing.append('advance')
        else:
            material = pieces.get(advance['issue_id'], {})
            missing.extend(str(number) for number in range(cutoff + 1, target + 1)
                           if str(number) not in material)
    if expansion:
        new = sorted(set(request['stations']) - set(state['stations']))
        if opening is None:
            missing.append('opening')
        else:
            material = pieces.get(opening['issue_id'], {})
            missing.extend(station for station in new if station not in material)
    plan = {'advance': advance, 'opening': opening, 'expansion': expansion}
    return missing, plan


def _settle_scoped(replica, request, state, plan, cutoff):
    target = request['through']
    branch = state['branch']
    facts = {observation_key(fact): fact for fact in read_replica_facts(replica)}
    if target > cutoff:
        material = request['pieces'][plan['advance']['issue_id']]
        apply_scoped_rows(facts, branch,
                          ((number, entry)
                           for number in range(cutoff + 1, target + 1)
                           for entry in material[str(number)]['rows']))
    if plan['expansion']:
        material = request['pieces'][plan['opening']['issue_id']]
        for station in sorted(material):
            for fact in material[station]['facts']:
                key = observation_key(fact)
                if key in facts:
                    raise Conflict('opening fact overlaps an existing observation')
                facts[key] = fact
    candidate = [facts[key] for key in sorted(facts)]
    return commit_scoped_publication(replica, request, candidate, expanded=plan['expansion'])


def receive_scoped_delivery(replica, request_id, *, day, branch, stations, through, parts=(),
                            on_stage=None):
    require_name(request_id, 'intake request')
    require_day(day)
    require_position(branch, 'branch', minimum=1)
    stations = require_station_set(stations)
    require_position(through, 'end')
    packages = [decode_scoped_package(part) for part in parts]
    with atomic(replica, write=True):
        state = read_scoped_state(replica)
        if state is None:
            full = count_publications(replica)
            full += replica.execute('SELECT COUNT(*) FROM replica_requests').fetchone()[0]
            if full:
                raise Conflict('replica follows whole-range handoff')
            state = {'day': day, 'branch': branch, 'stations': stations, 'expanded': False}
            begin_scoped_state(replica, day, branch, stations)
        if day != state['day'] or branch != state['branch']:
            raise Conflict('scoped request differs from the replica day or branch')
        cutoff = read_progress(replica)['boundary']['through']
        request = read_scoped_request(replica, request_id)
        if request is not None and (request['day'], request['branch'], request['stations'],
                                    request['through']) != (day, branch, stations, through):
            raise Conflict('scoped request already has different parameters')
        if request is None:
            _check_scoped_freshness(stations, through, state, cutoff)
            record_scoped_request(replica, request_id, day, branch, stations, through)
            request = read_scoped_request(replica, request_id)
        elif request['result'] is None:
            _check_scoped_freshness(request['stations'], request['through'], state, cutoff)
        declarations, pieces = _merge_scoped_parts(request, packages, state, cutoff)
        update_scoped_material(replica, request_id, declarations, pieces)
        if request['result'] is not None:
            return request['result']

    with atomic(replica, write=True):
        request = read_scoped_request(replica, request_id)
        if request['result'] is not None:
            return request['result']
        state = read_scoped_state(replica)
        progress = read_progress(replica)
        cutoff = progress['boundary']['through']
        _check_scoped_freshness(request['stations'], request['through'], state, cutoff)
        missing, plan = _scoped_missing(request, state, cutoff)
        if missing:
            return {'status': 'pending', 'request_id': request_id, 'missing': missing,
                    'boundary': progress['boundary']}
        receipt = _settle_scoped(replica, request, state, plan, cutoff)
        if on_stage is not None:
            on_stage('before_commit')
    if on_stage is not None:
        on_stage('after_commit')
    return receipt
