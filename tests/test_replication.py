from dataclasses import replace

from tests.fixtures import DAY, WarehouseCase, change, original_rows, scenario
from warehouse.export.issues import issue_delivery, read_issue_result
from warehouse.export.packages import package_fingerprint
from warehouse.replica.intake import read_receipt
from warehouse.replica.progress import count_publications, read_progress
from warehouse.report.lookup import lookup_daily, lookup_fixed_report
from warehouse.source.validation import ABSENT_OBSERVATION, Conflict


class ReplicationTests(WarehouseCase):
    def test_two_formal_corrections_offline(self):
        self.submit('opening', *original_rows())
        initial = self.issue('opening', 0, 1)
        self.submit('correct-a', change('A', 7, 11))
        last = self.submit('correct-b', change('B', 11, 13))
        package = self.issue('following', 1, 3)
        numbers = [batch['number'] for batch in package.batches]
        self.assertEqual(numbers, [2, 3])
        self.assertEqual(package.declaration['through'], 3)
        with self.replica() as replica:
            self.receive(replica, 'opening', initial)
            previous = lookup_daily(replica, DAY)
            readers_saw = []
            with self.replica() as reader:
                def observe(stage):
                    readers_saw.append((stage, read_progress(reader)['boundary'], lookup_daily(reader, DAY)['total_liters']))

                outcome = self.receive(replica, 'two-corrections', package, on_stage=observe)
            self.assertEqual(previous['total_liters'], 18)
            self.assertEqual(readers_saw, [('before_commit', {'branch': 1, 'through': 1}, 18),
                                           ('after_commit', {'branch': 1, 'through': 3}, 24)])
            report = lookup_fixed_report(replica, outcome['report_id'])
            self.assertEqual(lookup_daily(replica, DAY), report)
            self.assertEqual((report['total_liters'], report['missing']), (24, 1))
            self.assertEqual(report['lines'][1]['provenance']['source_receipt'], last)
            self.assertEqual(report['boundary'], {'branch': 1, 'through': 3})
            self.assertEqual(report['intake_receipt'], outcome)
            self.assertEqual(self.receive(replica, 'two-corrections', package), outcome)
            self.assertEqual(count_publications(replica), 2)
        scenario('V3', {'start': 1, 'end': 3, 'changes': [batch['changes'] for batch in package.batches]},
                 [2, 3], numbers, [24, 3], [report['total_liters'], report['boundary']['through']],
                 ['warehouse/export/ranges.py', 'warehouse/export/issues.py', 'warehouse/replica/materialize.py',
                  'warehouse/replica/activation.py'], 'The range includes both corrections. 11 + 13 = 24 L.')

    def test_ordered_preimages_and_incomplete_range(self):
        self.submit('opening', change('K', ABSENT_OBSERVATION, 7))
        initial = self.issue('opening', 0, 1)
        self.submit('first', change('K', 7, 11))
        final_source = self.submit('second', change('K', 11, 17))
        package = self.issue('to-three', 1, 3)
        with self.replica() as replica:
            self.receive(replica, 'opening', initial)
            partial = self.receive(replica, 'corrections', replace(package, batches=package.batches[1:]))
            self.assertEqual(partial['missing'], ['formal_range'])
            self.assertEqual(lookup_daily(replica, DAY)['total_liters'], 7)
            self.assertIsNone(read_receipt(replica, 'corrections'))
            result = self.receive(replica, 'corrections', replace(package, batches=package.batches[:1]))
            self.assertEqual(result['boundary'], {'branch': 1, 'through': 3})
            fixed = lookup_fixed_report(replica, result['report_id'])
            self.assertEqual(fixed['total_liters'], 17)
            self.assertEqual(fixed['lines'][0]['provenance']['source_receipt'], final_source)

    def test_conflicting_range_and_preimage_leave_old_publication(self):
        self.submit('opening', change('K', ABSENT_OBSERVATION, 7))
        initial = self.issue('opening', 0, 1)
        self.submit('next', change('K', 7, 11))
        package = self.issue('next', 1, 2)
        changed_batch = dict(package.batches[0], changes=[change('K', 9, 11)])
        changed = replace(package, batches=(changed_batch,))
        with self.replica() as replica:
            self.receive(replica, 'opening', initial)
            with self.assertRaisesRegex(Conflict, 'content does not match'):
                self.receive(replica, 'tampered', changed)
            conflicting = replace(changed, declaration=dict(package.declaration,
                                                            batch_hashes=[package_fingerprint(changed_batch)]))
            with self.assertRaisesRegex(Conflict, 'preimage conflict'):
                self.receive(replica, 'wrong-preimage', conflicting)
            self.assertEqual(lookup_daily(replica, DAY)['total_liters'], 7)
            self.assertEqual(read_progress(replica)['boundary'], {'branch': 1, 'through': 1})
            self.assertEqual(count_publications(replica), 1)
            self.assertIsNone(read_receipt(replica, 'wrong-preimage'))
            gap = self.receive(replica, 'valid', package)
            self.assertEqual(gap['boundary']['through'], 2)
            with self.assertRaisesRegex(Conflict, 'before the current boundary'):
                self.receive(replica, 'overlap', package)

    def test_issue_pending_then_sealed_and_exact_replay(self):
        self.submit('opening', change('K', ABSENT_OBSERVATION, 7))
        args = {'day': DAY, 'branch': 1, 'after': 0, 'through': 2}
        pending = issue_delivery(self.root, 'first-two', **args)
        self.assertEqual((pending['status'], pending['missing'], pending['package']),
                         ('pending', ['formal_range'], None))
        with self.assertRaises(Conflict):
            issue_delivery(self.root, 'first-two', **dict(args, through=1))
        self.submit('second', change('K', 7, 11))
        sealed = issue_delivery(self.root, 'first-two', **args)
        self.assertEqual(sealed['status'], 'sealed')
        self.submit('later', change('K', 11, 17))
        self.assertEqual(read_issue_result(self.root, 'first-two'), sealed)
        self.assertEqual(issue_delivery(self.root, 'first-two', **args), sealed)
        self.assertIsNone(read_issue_result(self.root, 'unknown'))

    def test_future_regular_range_waits_for_its_predecessor(self):
        self.submit('opening', change('K', ABSENT_OBSERVATION, 7))
        initial = self.issue('opening', 0, 1)
        self.submit('next', change('K', 7, 11))
        later = self.issue('next', 1, 2)
        with self.replica() as replica:
            waiting = self.receive(replica, 'late-range', later)
            self.assertEqual(waiting['missing'], ['preceding_publication'])
            self.assertIsNone(read_receipt(replica, 'late-range'))
            self.receive(replica, 'opening', initial)
        with self.replica() as replica:
            replay = self.receive(replica, 'late-range', later)
            self.assertEqual(replay['boundary'], {'branch': 1, 'through': 2})
            self.assertEqual(lookup_daily(replica, DAY)['total_liters'], 11)
