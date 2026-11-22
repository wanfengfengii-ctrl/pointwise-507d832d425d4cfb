from tests.fixtures import DAY, NORTH, SOUTH, WarehouseCase, change, original_rows, scenario
from warehouse.recovery.workspace import open_active_workspace
from warehouse.report.lookup import lookup_daily, lookup_fixed_report
from warehouse.source.batches import read_formal_batches, read_source_boundary
from warehouse.source.receipts import find_source_receipt, read_batch_receipt
from warehouse.source.store import initialize_source
from warehouse.source.validation import ABSENT_OBSERVATION, Conflict, NotFound, WarehouseError


class SourceDailyTests(WarehouseCase):
    def test_initial_report_and_reopen(self):
        rows = original_rows()
        receipt = self.submit('morning', *rows)
        fixed = lookup_daily(self.root, DAY, freeze=True)
        for _ in range(2):
            with open_active_workspace(self.root) as source:
                initialize_source(source)
                self.assertEqual(find_source_receipt(source, 'morning'), receipt)
                self.assertEqual(read_batch_receipt(source, 1, 1), receipt)
        after = lookup_daily(self.root, DAY)
        self.assertEqual((len(after['lines']), after['total_liters'], after['missing']), (3, 18, 1))
        self.assertEqual([line['key'] for line in after['lines']], ['A', 'B', 'C'])
        self.assertEqual([line['provenance']['row'] for line in after['lines']], [3, 1, 2])
        old = lookup_fixed_report(self.root, fixed['report_id'])
        self.assertEqual(old, fixed)
        self.assertEqual(old['lines'][0]['provenance']['source_receipt'], receipt)
        with self.assertRaises(NotFound):
            lookup_fixed_report(self.root, 'unknown')
        actual = [len(after['lines']), after['total_liters'], after['missing']]
        scenario('V1', {'rows': rows}, [3, 18, 1], actual, 18, old['total_liters'],
                 ['warehouse/source/batches.py', 'warehouse/source/store.py', 'warehouse/report/lookup.py',
                  'warehouse/report/daily.py', 'warehouse/report/archive.py'],
                 'A and B contribute 7 + 11 liters. C is one missing row.')

    def test_reopen_preserves_formal_correction_and_receipt_replay(self):
        original = self.submit('morning', *original_rows())
        corrected = self.submit('correction', change('A', 7, 13))
        for _ in range(2):
            with open_active_workspace(self.root) as source:
                initialize_source(source)
                self.assertEqual(find_source_receipt(source, 'morning'), original)
                self.assertEqual(find_source_receipt(source, 'correction'), corrected)
                self.assertEqual([batch['number'] for batch in read_formal_batches(source, 1, 0, 2)], [1, 2])
        self.assertEqual(self.submit('correction', change('A', 7, 13)), corrected)
        with self.assertRaises(Conflict):
            self.submit('correction', change('A', 7, 99))
        report = lookup_daily(self.root, DAY)
        self.assertEqual((len(report['lines']), report['total_liters'], report['missing']), (3, 24, 1))
        self.assertEqual(report['lines'][0]['provenance']['source_receipt'], corrected)

    def test_missing_zero_order_and_station_totals(self):
        rows = original_rows() + [change('D', ABSENT_OBSERVATION, 0)]
        receipt = self.submit('morning', *rows)
        fixed = lookup_daily(self.root, DAY, freeze=True)
        self.assertEqual((len(fixed['lines']), fixed['total_liters'], fixed['missing'], fixed['measured']),
                         (4, 18, 1, 3))
        values = {line['key']: line['liters'] for line in fixed['lines']}
        self.assertIsNone(values['C'])
        self.assertIs(type(values['D']), int)
        self.assertEqual(values['D'], 0)
        self.assertTrue(all(line['provenance']['source_receipt'] == receipt for line in fixed['lines']))
        self.submit('south', change('Z', ABSENT_OBSERVATION, 5, station=SOUTH),
                    change('A', ABSENT_OBSERVATION, 2, station=SOUTH))
        current = lookup_daily(self.root, DAY)
        self.assertEqual([(r['station'], r['key']) for r in current['lines']],
                         [(NORTH, 'A'), (NORTH, 'B'), (NORTH, 'C'), (NORTH, 'D'), (SOUTH, 'A'), (SOUTH, 'Z')])
        self.assertEqual(current['stations'], [{'station': NORTH, 'liters': 18, 'missing': 1, 'measured': 3},
                                               {'station': SOUTH, 'liters': 7, 'missing': 0, 'measured': 2}])
        self.assertEqual(current['total_liters'], 25)
        self.assertEqual(lookup_fixed_report(self.root, fixed['report_id']), fixed)

    def test_late_preimage_conflict_rejects_entire_batch(self):
        original = self.submit('morning', *original_rows())
        proposed = [change('A', 7, 13), change('B', 9, 19)]
        with self.assertRaisesRegex(Conflict, 'preimage conflict'):
            self.submit('bad-correction', *proposed)
        with open_active_workspace(self.root) as source:
            self.assertIsNone(find_source_receipt(source, 'bad-correction'))
            self.assertIsNone(read_batch_receipt(source, 1, 2))
            self.assertEqual(read_source_boundary(source), {'branch': 1, 'through': 1})
            self.assertEqual(find_source_receipt(source, 'morning'), original)
        fixed = lookup_daily(self.root, DAY, freeze=True)
        actual = [row['liters'] for row in fixed['lines']]
        self.assertEqual(actual, [7, 11, None])
        self.assertEqual(fixed['total_liters'], 18)
        scenario('V2', proposed, [7, 11, None], actual, 18, fixed['total_liters'],
                 ['warehouse/source/batches.py', 'warehouse/source/facts.py', 'warehouse/report/daily.py'],
                 'The second row claims 9 L for a key recorded at 11 L, so all rows retain their prior values.')

    def test_batch_order_and_strict_inputs(self):
        with self.assertRaises(WarehouseError):
            self.submit('bool', change('A', ABSENT_OBSERVATION, False))
        with self.assertRaises(WarehouseError):
            self.submit('bad-day', change('A', ABSENT_OBSERVATION, 0, day='2026-02-30'))
        receipt = self.submit('ordered', change('A', ABSENT_OBSERVATION, None), change('A', None, 0),
                              change('A', 0, 7))
        self.assertEqual(receipt['number'], 1)
        self.assertEqual(receipt['row_count'], 3)
        with self.assertRaises(Conflict):
            self.submit('bad-before', change('A', ABSENT_OBSERVATION, 9))
        line = lookup_daily(self.root, DAY)['lines'][0]
        self.assertEqual((line['before'], line['liters'], line['provenance']['row']), (0, 7, 3))
