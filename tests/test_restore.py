from contextlib import closing
from copy import deepcopy
from dataclasses import replace

from tests.fixtures import DAY, WarehouseCase, change, original_rows, scenario
from warehouse.export.issues import issue_delivery
from warehouse.recovery.catalog import open_control_catalog, read_checkpoint_state
from warehouse.recovery.checkpoints import capture_checkpoint, read_checkpoint
from warehouse.recovery.restore import read_restore_result, restore_source
from warehouse.recovery.workspace import open_active_workspace
from warehouse.replica.intake import read_receipt
from warehouse.replica.progress import read_progress
from warehouse.report.lookup import lookup_daily, lookup_fixed_report
from warehouse.source.batches import read_source_boundary
from warehouse.source.receipts import read_batch_receipt
from warehouse.source.validation import ABSENT_OBSERVATION, Conflict, NotFound


class RestoreTests(WarehouseCase):
    def test_material_ready_checkpoint_requires_formal_registration(self):
        original = self.submit('morning', *original_rows())
        prepared = capture_checkpoint(self.root, 'staged', 1, 1, finalize=False)
        self.assertEqual((prepared['status'], prepared['material_ready']), ('preparing', True))
        with closing(open_control_catalog(self.root)) as control:
            self.assertIsNotNone(read_checkpoint_state(control, 'staged')['material'])
            with self.assertRaises(NotFound):
                read_checkpoint(control, 'staged')
        with self.assertRaises(NotFound):
            restore_source(self.root, 'restore-staged', 'staged')
        self.assertIsNone(read_restore_result(self.root, 'restore-staged'))
        args = {'day': DAY, 'branch': 2, 'after': 1, 'through': 2, 'restore_id': 'restore-staged'}
        pending = issue_delivery(self.root, 'awaiting-restore', **args)
        self.assertEqual((pending['status'], pending['missing'], pending['package']),
                         ('pending', ['restore_registration'], None))
        correction = self.submit('continue-original', change('A', 7, 13))
        with open_active_workspace(self.root) as source:
            self.assertEqual(read_batch_receipt(source, 1, 1), original)
            self.assertEqual(read_source_boundary(source), {'branch': 1, 'through': 2})
        old = lookup_daily(self.root, DAY)
        self.assertEqual((correction['branch'], old['total_liters']), (1, 24))
        capture_checkpoint(self.root, 'staged', 1, 1)
        self.assertEqual(restore_source(self.root, 'restore-staged', 'staged')['branch'], 2)
        self.assertEqual(issue_delivery(self.root, 'awaiting-restore', **args)['missing'], ['formal_range'])
        self.submit('continue-restored', change('A', 7, 17))
        self.assertEqual(issue_delivery(self.root, 'awaiting-restore', **args)['status'], 'sealed')
        scenario('V4', {'checkpoint': 'staged', 'before_registration': True}, ['preparing', 'pending'],
                 [prepared['status'], pending['status']], [1, 24], [old['boundary']['branch'], old['total_liters']],
                 ['warehouse/recovery/checkpoints.py', 'warehouse/recovery/catalog.py',
                  'warehouse/recovery/restore.py', 'warehouse/export/issues.py'],
                 'Before formal registration the active branch stays 1 and its valid correction yields 13 + 11 L.')

    def test_reused_number_cuts_over_ahead_and_behind_replicas(self):
        state = self.recovery_case()
        old_source, new_source = state['old_receipt'], state['new_receipt']
        self.assertEqual((old_source['number'], new_source['number']), (11, 11))
        self.assertEqual((old_source['branch'], new_source['branch']), (1, 2))
        totals = []
        with self.replica('ahead') as ahead, self.replica('behind') as behind:
            original = self.receive(ahead, 'old-ahead', state['ahead'])
            self.receive(behind, 'old-behind', state['behind'])
            self.assertEqual(lookup_daily(ahead, DAY)['total_liters'], 11)
            self.assertEqual(lookup_daily(behind, DAY)['total_liters'], 5)
            for replica in (ahead, behind):
                outcome = self.receive(replica, 'handoff', state['handoff'])
                fixed = lookup_fixed_report(replica, outcome['report_id'])
                self.assertEqual(outcome['boundary'], {'branch': 2, 'through': 11})
                self.assertEqual(fixed['total_liters'], 17)
                self.assertEqual(fixed['lines'][0]['before'], 7)
                self.assertEqual(fixed['lines'][0]['provenance']['source_receipt'], new_source)
                self.assertEqual(fixed['intake_receipt'], outcome)
                totals.append(fixed['total_liters'])
            old = lookup_fixed_report(ahead, original['report_id'])
            self.assertEqual(old['total_liters'], 11)
            self.assertEqual(old['lines'][0]['provenance']['source_receipt'], old_source)
            self.assertEqual(old['intake_receipt'], original)
        scenario('V5', {'behind': 5, 'ahead': 11, 'checkpoint': 7, 'correction': [7, 17]}, [17, 17], totals,
                 11, old['total_liters'], ['warehouse/export/restore_handoff.py', 'warehouse/replica/intake.py',
                                           'warehouse/replica/progress.py', 'warehouse/report/lookup.py'],
                 'Replay 7 to 17 from the formal tenth batch for both replicas. The old fixed 11 L is immutable.')

    def test_branch_identity_survives_identical_batch_body(self):
        state = self.recovery_case(new_value=11)
        old_batch = state['ahead'].batches[-1]
        new_batch = state['handoff'].batches[0]
        self.assertEqual(old_batch['changes'], new_batch['changes'])
        self.assertEqual(old_batch['number'], new_batch['number'])
        with self.replica() as replica:
            previous = self.receive(replica, 'old', state['ahead'])
            current = self.receive(replica, 'new', state['handoff'])
            old = lookup_fixed_report(replica, previous['report_id'])
            new = lookup_fixed_report(replica, current['report_id'])
        self.assertEqual((old['total_liters'], new['total_liters']), (11, 11))
        origins = [result['lines'][0]['provenance'] for result in (old, new)]
        self.assertEqual([(origin['branch'], origin['batch'], origin['row']) for origin in origins],
                         [(1, 11, 1), (2, 11, 1)])
        self.assertNotEqual(origins[0]['source_receipt']['receipt_id'], origins[1]['source_receipt']['receipt_id'])

    def test_partial_handoff_reopen_and_checkpoint_binding(self):
        state = self.recovery_case()
        handoff = state['handoff']
        partial = replace(handoff, basis=None)
        with self.replica() as replica:
            self.receive(replica, 'old-behind', state['behind'])
            old = lookup_daily(replica, DAY)
            pending = self.receive(replica, 'switch', partial)
            self.assertEqual(pending['missing'], ['restore_basis'])
            self.assertEqual(pending['boundary'], {'branch': 1, 'through': 9})
            self.assertEqual(lookup_daily(replica, DAY), old)
            self.assertIsNone(read_receipt(replica, 'switch'))
        with self.replica() as replica:
            basis = deepcopy(handoff.basis)
            basis['boundary']['through'] = 11
            with self.assertRaisesRegex(Conflict, 'different checkpoint'):
                self.receive(replica, 'switch', replace(handoff, basis=basis))
            same_boundary = deepcopy(handoff.basis)
            same_boundary['facts'][0]['liters'] = 5
            with self.assertRaisesRegex(Conflict, 'differs from the sealed checkpoint'):
                self.receive(replica, 'switch', replace(handoff, basis=same_boundary))
            self.assertEqual(read_progress(replica)['boundary'], {'branch': 1, 'through': 9})
            supplement = replace(handoff, batches=())
            result = self.receive(replica, 'switch', supplement)
            fixed = lookup_fixed_report(replica, result['report_id'])
            self.assertEqual(result['boundary'], {'branch': 2, 'through': 11})
            self.assertEqual(fixed['total_liters'], 17)
            self.assertEqual(fixed['intake_receipt'], result)
        scenario('V6', {'early_value': 5, 'basis': 'missing then exact checkpoint 10'}, ['pending', 9],
                 [pending['status'], pending['boundary']['through']], [17, 11],
                 [fixed['total_liters'], fixed['boundary']['through']],
                 ['warehouse/export/packages.py', 'warehouse/replica/requests.py', 'warehouse/replica/intake.py'],
                 'No 7 L before-image is available until the exact checkpoint arrives. The saved correction makes 17 L.')

    def test_historical_checkpoint_and_restore_replay_preserve_receipts(self):
        opening = self.submit('morning', *original_rows())
        old_fixed = lookup_daily(self.root, DAY, freeze=True)
        self.submit('later', change('A', 7, 11))
        frozen = capture_checkpoint(self.root, 'historical', 1, 1)
        self.assertEqual(frozen['boundary'], {'branch': 1, 'through': 1})
        with closing(open_control_catalog(self.root)) as control:
            checkpoint = read_checkpoint(control, 'historical')
            self.assertEqual([row['liters'] for row in checkpoint['facts']], [7, 11, None])
            self.assertEqual([batch['receipt'] for batch in checkpoint['history']], [opening])
        capture_checkpoint(self.root, 'another', 1, 2)
        restore = restore_source(self.root, 'return', 'historical')
        second = self.submit('correction', change('A', 7, 17))
        self.assertEqual(restore_source(self.root, 'return', 'historical'), restore)
        self.assertEqual(read_restore_result(self.root, 'return'), restore)
        with self.assertRaises(Conflict):
            restore_source(self.root, 'return', 'another')
        with open_active_workspace(self.root) as source:
            self.assertEqual(read_batch_receipt(source, 1, 1), opening)
            self.assertEqual(read_batch_receipt(source, 2, 2), second)
        self.assertEqual(lookup_daily(self.root, DAY)['total_liters'], 28)
        self.assertEqual(lookup_fixed_report(self.root, old_fixed['report_id']), old_fixed)

    def test_empty_restore_range_and_subsequent_branch(self):
        self.submit('opening', change('K', ABSENT_OBSERVATION, 7))
        capture_checkpoint(self.root, 'base', 1, 1)
        restore_source(self.root, 'second', 'base')
        empty = self.issue('empty', 1, 1, branch=2, restore_id='second')
        self.assertEqual(empty.batches, ())
        self.submit('correction', change('K', 7, 17))
        next_package = self.issue('second-more', 1, 2, branch=2)
        capture_checkpoint(self.root, 'second-base', 2, 2)
        restore_source(self.root, 'third', 'second-base')
        self.submit('third-correction', change('K', 17, 19))
        third = self.issue('third-first', 2, 3, branch=3, restore_id='third')
        with self.replica() as replica:
            self.assertEqual(self.receive(replica, 'switch-empty', empty)['boundary'], {'branch': 2, 'through': 1})
            self.assertEqual(lookup_daily(replica, DAY)['total_liters'], 7)
            self.receive(replica, 'second-range', next_package)
            self.assertEqual(lookup_daily(replica, DAY)['total_liters'], 17)
            self.assertEqual(self.receive(replica, 'switch-third', third)['boundary'], {'branch': 3, 'through': 3})
            self.assertEqual(lookup_daily(replica, DAY)['total_liters'], 19)
