import os
from contextlib import closing, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from warehouse.recovery.catalog import active_branch, open_control_catalog
from warehouse.recovery.identity import read_branch_identity
from warehouse.source.batches import insert_formal_batch
from warehouse.source.facts import read_facts
from warehouse.source.store import atomic, open_source
from warehouse.source.validation import Conflict


def workspace_path(root, branch):
    return Path(root) / f'branch-{branch}.sqlite3'


@contextmanager
def open_branch_workspace(root, control, branch, *, create_initial=False):
    identity = read_branch_identity(control, branch)
    path = workspace_path(root, identity.branch)
    existing = not (identity.branch == 1 and create_initial and not path.is_file())
    with closing(open_source(path, identity.branch, existing=existing)) as source:
        yield source


@contextmanager
def open_active_workspace(root):
    with closing(open_control_catalog(root)) as control, atomic(control, write=True):
        with open_branch_workspace(root, control, active_branch(control), create_initial=True) as source:
            yield source


def prepare_restored_workspace(root, identity, checkpoint):
    boundary = checkpoint['boundary']
    if (checkpoint['checkpoint_id'], boundary['branch'], boundary['through']) != (
            identity.checkpoint_id, identity.base_branch, identity.base_through):
        raise Conflict('workspace preparation requires the allocated checkpoint')
    history = checkpoint['history']
    if len(history) != identity.base_through:
        raise Conflict('checkpoint history lacks its formal prefix')
    target = workspace_path(root, identity.branch)
    with TemporaryDirectory(prefix='prepare-', dir=root) as directory:
        staged = Path(directory) / target.name
        with closing(open_source(staged, identity.branch)) as source, atomic(source, write=True):
            last_branch = 1
            for expected, batch in enumerate(history, 1):
                if batch['number'] != expected or not last_branch <= batch['branch'] <= identity.base_branch:
                    raise Conflict('checkpoint history is out of formal order')
                insert_formal_batch(source, batch)
                last_branch = batch['branch']
            if read_facts(source) != checkpoint['facts']:
                raise Conflict('checkpoint facts differ from its formal history')
            source.execute('UPDATE source_head SET base = ?, through = ? WHERE singleton = 1',
                           (identity.base_through, identity.base_through))
        os.replace(staged, target)
    return target
