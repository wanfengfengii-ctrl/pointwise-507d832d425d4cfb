from dataclasses import dataclass

from warehouse.recovery.catalog import active_branch, find_branch_record
from warehouse.source.validation import NotFound, require_position


@dataclass(frozen=True)
class BranchIdentity:
    branch: int
    restore_id: str | None
    checkpoint_id: str | None
    base_branch: int | None
    base_through: int


def allocate_branch_identity(control, restore_id, checkpoint):
    boundary = checkpoint['boundary']
    return BranchIdentity(require_position(active_branch(control) + 1, 'branch', minimum=1),
                          restore_id, checkpoint['checkpoint_id'], boundary['branch'], boundary['through'])


def read_branch_identity(control, branch):
    require_position(branch, 'branch', minimum=1)
    if branch == 1:
        return BranchIdentity(1, None, None, None, 0)
    record = find_branch_record(control, branch)
    if record is None:
        raise NotFound(f'unknown branch {branch}')
    boundary = record['base_boundary']
    return BranchIdentity(branch, record['restore_id'], record['checkpoint_id'],
                          boundary['branch'], boundary['through'])
