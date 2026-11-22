from contextlib import closing

from warehouse.recovery.catalog import active_branch, find_restore_record, open_control_catalog, record_restore_result
from warehouse.recovery.checkpoints import read_checkpoint
from warehouse.recovery.identity import allocate_branch_identity
from warehouse.recovery.workspace import prepare_restored_workspace
from warehouse.source.store import atomic
from warehouse.source.validation import Conflict, require_name


def restore_source(root, restore_id, checkpoint_id):
    require_name(restore_id, 'restore request')
    require_name(checkpoint_id, 'checkpoint')
    with closing(open_control_catalog(root)) as control, atomic(control, write=True):
        previous = find_restore_record(control, restore_id)
        if previous is not None:
            if previous['checkpoint_id'] != checkpoint_id:
                raise Conflict('restore request already has a different checkpoint')
            return previous
        checkpoint = read_checkpoint(control, checkpoint_id)
        identity = allocate_branch_identity(control, restore_id, checkpoint)
        prepared = prepare_restored_workspace(root, identity, checkpoint)
        result = {'restore_id': restore_id, 'checkpoint_id': checkpoint_id, 'branch': identity.branch,
                  'previous_branch': active_branch(control), 'base_boundary': checkpoint['boundary'],
                  'workspace': prepared.name}
        record_restore_result(control, result)
        return result


def read_restore_result(root, restore_id):
    with closing(open_control_catalog(root)) as control:
        return find_restore_record(control, restore_id)
