import json
from contextlib import closing

from warehouse.recovery.catalog import find_formal_checkpoint, open_control_catalog, record_checkpoint_state
from warehouse.recovery.workspace import open_branch_workspace
from warehouse.source.batches import read_formal_history, read_source_limits
from warehouse.source.facts import project_facts, read_facts
from warehouse.source.store import atomic
from warehouse.source.validation import Conflict, NotFound, require_name, require_position


def _freeze(source, checkpoint_id, boundary):
    through = boundary['through']
    with atomic(source):
        limits = read_source_limits(source)
        if through < limits['base']:
            raise Conflict('requested boundary predates this branch')
        if through > limits['through']:
            return None
        history = read_formal_history(source, through)
        if len(history) != through:
            raise Conflict('formal history is incomplete at the checkpoint')
        facts = read_facts(source) if through == limits['through'] else project_facts([], history)
    return {'checkpoint_id': checkpoint_id, 'boundary': boundary, 'facts': facts, 'history': history}


def _material(record):
    data = json.loads(record['material'])
    if data['checkpoint_id'] != record['checkpoint_id'] or data['boundary'] != record['boundary']:
        raise Conflict('checkpoint material differs from its registration')
    return data


def capture_checkpoint(root, checkpoint_id, branch, through, *, finalize=True):
    require_name(checkpoint_id, 'checkpoint')
    boundary = {'branch': require_position(branch, 'branch', minimum=1),
                'through': require_position(through, 'checkpoint boundary')}
    with closing(open_control_catalog(root)) as control:
        with atomic(control, write=True):
            record = record_checkpoint_state(control, checkpoint_id, boundary)
            if record['material'] is None:
                with open_branch_workspace(root, control, branch, create_initial=True) as source:
                    data = _freeze(source, checkpoint_id, boundary)
                if data is not None:
                    record = record_checkpoint_state(control, checkpoint_id, boundary, data)
        if finalize and record['status'] == 'preparing' and record['material'] is not None:
            with atomic(control, write=True):
                data = _material(record)
                record = record_checkpoint_state(control, checkpoint_id, boundary, data, formal=True)
    return {'checkpoint_id': checkpoint_id, 'boundary': boundary, 'status': record['status'],
            'material_ready': record['material'] is not None}


def read_checkpoint(control, checkpoint_id):
    record = find_formal_checkpoint(control, checkpoint_id)
    if record is None:
        raise NotFound(f'formal checkpoint {checkpoint_id!r} unavailable')
    return _material(record)
