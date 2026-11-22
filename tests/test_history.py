from dataclasses import replace

from tests.fixtures import DAY, WarehouseCase, scenario
from warehouse.replica.intake import read_receipt
from warehouse.replica.progress import count_publications, read_progress
from warehouse.replica.requests import read_intake_request
from warehouse.report.lookup import lookup_daily, lookup_fixed_report
from warehouse.source.validation import Conflict


class LostReply(RuntimeError):
    pass


class HistoryTests(WarehouseCase):
    def test_old_delivery_and_original_rows(self):
        state = self.recovery_case(missing=True)
        with self.replica() as replica:
            old_receipt = self.receive(replica, 'old', state['ahead'])
            old_report = lookup_fixed_report(replica, old_receipt['report_id'])
            new_receipt = self.receive(replica, 'new', state['handoff'])
            new_report = lookup_daily(replica, DAY)
        with self.replica() as replica:
            with self.assertRaisesRegex(Conflict, 'earlier branch'):
                self.receive(replica, 'late-old', state['late'])
            self.assertIsNone(read_receipt(replica, 'late-old'))
            self.assertEqual(self.receive(replica, 'old', state['ahead']), old_receipt)
            self.assertEqual(read_progress(replica)['boundary'], {'branch': 2, 'through': 11})
            self.assertEqual(count_publications(replica), 2)
            current = lookup_daily(replica, DAY)
            historical = lookup_fixed_report(replica, old_receipt['report_id'])
            self.assertEqual(current, new_report)
            self.assertEqual(historical, old_report)
        self.assertEqual((current['total_liters'], current['missing']), (17, 1))
        self.assertEqual((historical['total_liters'], historical['missing']), (11, 1))
        self.assertEqual([(line['key'], line['liters']) for line in historical['lines']], [('K', 11), ('M', None)])
        self.assertEqual(historical['lines'][0]['provenance']['source_receipt'], state['old_receipt'])
        self.assertEqual(historical['lines'][1]['provenance']['batch'], 10)
        self.assertEqual(historical['boundary'], {'branch': 1, 'through': 11})
        self.assertEqual(historical['intake_receipt'], old_receipt)
        self.assertEqual(current['intake_receipt'], new_receipt)
        scenario('V7', {'late_branch': 1, 'late_batch': 12, 'late_value': 13}, [17, 2, 11],
                 [current['total_liters'], current['boundary']['branch'], current['boundary']['through']],
                 [11, 1], [historical['total_liters'], historical['missing']],
                 ['warehouse/replica/intake.py', 'warehouse/replica/progress.py', 'warehouse/report/archive.py',
                  'warehouse/report/provenance.py'], 'The old receipt records 11 L and one missing row. The late 13 L is fenced out.')

    def test_before_commit_rollback_and_after_commit_replay(self):
        state = self.recovery_case()
        handoff = state['handoff']
        with self.replica() as replica:
            old = self.receive(replica, 'old', state['behind'])
            with self.replica() as reader:
                def interrupt(stage):
                    self.assertEqual(stage, 'before_commit')
                    self.assertEqual(read_progress(reader)['boundary'], {'branch': 1, 'through': 9})
                    self.assertEqual(lookup_daily(reader, DAY)['total_liters'], 5)
                    self.assertIsNone(read_receipt(reader, 'switch'))
                    raise LostReply('publication not committed')

                with self.assertRaisesRegex(LostReply, 'not committed'):
                    self.receive(replica, 'switch', handoff, on_stage=interrupt)
        after_commit = []
        with self.replica() as replica:
            self.assertIsNone(read_receipt(replica, 'switch'))
            self.assertIsNotNone(read_intake_request(replica, 'switch'))
            self.assertEqual(read_progress(replica)['report_id'], old['report_id'])
            self.assertEqual(count_publications(replica), 1)
            other = replace(handoff, declaration=dict(handoff.declaration, issue_id='another-issue'))
            with self.assertRaisesRegex(Conflict, 'different declaration'):
                self.receive(replica, 'switch', other)

            def lose_reply(stage):
                if stage == 'after_commit':
                    current = lookup_daily(replica, DAY)
                    self.assertEqual((current['total_liters'], current['boundary']),
                                     (17, {'branch': 2, 'through': 11}))
                    after_commit.append(current['intake_receipt'])
                    raise LostReply('committed before return')

            with self.assertRaisesRegex(LostReply, 'committed before return'):
                self.receive(replica, 'switch', replace(handoff, batches=(), basis=None), on_stage=lose_reply)
        with self.replica() as replica:
            receipt = read_receipt(replica, 'switch')
            self.assertEqual(receipt, after_commit[0])
            self.assertEqual(self.receive(replica, 'switch', handoff), receipt)
            with self.assertRaisesRegex(Conflict, 'different declaration'):
                self.receive(replica, 'switch', other)
            self.assertEqual(count_publications(replica), 2)
            current = lookup_fixed_report(replica, receipt['report_id'])
            self.assertEqual(current, lookup_daily(replica, DAY))
            self.assertEqual(current['intake_receipt'], receipt)
            self.assertEqual(current['lines'][0]['provenance']['source_receipt'], state['new_receipt'])
            self.assertEqual(lookup_fixed_report(replica, old['report_id'])['total_liters'], 5)
        scenario('V8', {'request': 'switch', 'interruptions': ['before_commit', 'after_commit']},
                 ['replica:switch', 'intake:switch'], [receipt['report_id'], receipt['receipt_id']], 17,
                 current['total_liters'], ['warehouse/replica/activation.py', 'warehouse/replica/requests.py',
                                            'warehouse/replica/intake.py', 'warehouse/report/lookup.py'],
                 'The one completed switch owns the report and receipt for its request. 7 to 17 yields 17 L.')
