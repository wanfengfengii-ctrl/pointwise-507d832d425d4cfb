from contextlib import closing

from warehouse.recovery.catalog import find_restore_record, open_control_catalog
from warehouse.recovery.checkpoints import read_checkpoint
from warehouse.recovery.identity import read_branch_identity
from warehouse.source.store import atomic
from warehouse.source.validation import Conflict, NotFound


def read_restore_evidence(root, restore_id):
    with closing(open_control_catalog(root)) as control, atomic(control):
        record = find_restore_record(control, restore_id)
        if record is None:
            return {'restore': None, 'checkpoint': None}
        identity = read_branch_identity(control, record['branch'])
        try:
            checkpoint = read_checkpoint(control, identity.checkpoint_id)
        except NotFound:
            return {'restore': record, 'checkpoint': None}
        if (checkpoint['boundary'] != record['base_boundary'] or
                checkpoint['boundary'] != {'branch': identity.base_branch, 'through': identity.base_through}):
            raise Conflict('restore registration and formal checkpoint disagree')
        return {'restore': record, 'checkpoint': checkpoint}


def find_missing_restore_evidence(evidence):
    if evidence['restore'] is None:
        return ['restore_registration']
    if evidence['checkpoint'] is None:
        return ['formal_checkpoint']
    return []


def portable_restore_basis(evidence, branch, after):
    record, checkpoint = evidence['restore'], evidence['checkpoint']
    if record['branch'] != branch or record['base_boundary']['through'] != after:
        raise Conflict('delivery range does not start at its registered restore boundary')
    return {'checkpoint_id': checkpoint['checkpoint_id'], 'boundary': checkpoint['boundary'],
            'facts': checkpoint['facts']}
