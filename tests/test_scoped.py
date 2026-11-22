import json

from tests.fixtures import DAY, NORTH, SOUTH, WarehouseCase, change
from warehouse.export.issues import issue_scoped_delivery, read_issue_result
from warehouse.replica.intake import read_receipt, receive_scoped_delivery
from warehouse.replica.progress import read_progress
from warehouse.report.lookup import lookup_daily, lookup_fixed_report
from warehouse.source.validation import ABSENT_OBSERVATION, Conflict, WarehouseError


def scoped_batches(case):
    case.submit('b1', change('A', ABSENT_OBSERVATION, 7), change('B', ABSENT_OBSERVATION, 5, station=SOUTH))
    case.submit('b2', change('A', 7, 11))
    case.submit('b3', change('B', 5, 13, station=SOUTH))
    case.submit('b4', change('B', 13, 17, station=SOUTH), change('A', 11, 19))
    case.submit('b5', change('B', 17, 23, station=SOUTH))


def subset(encoded, keys):
    packet = json.loads(encoded)
    packet['pieces'] = {key: value for key, value in packet['pieces'].items() if key in keys}
    return json.dumps(packet, ensure_ascii=False).encode('utf-8')


class ScopedHandoffTests(WarehouseCase):
    def issue_scoped(self, issue_id, stations, after, through, mode):
        result = issue_scoped_delivery(self.root, issue_id, day=DAY, branch=1, stations=stations,
                                       after=after, through=through, mode=mode)
        self.assertEqual(result['status'], 'sealed')
        return result['package']

    def receive_scoped(self, replica, name, stations, through, parts=(), **kwargs):
        return receive_scoped_delivery(replica, name, day=DAY, branch=1, stations=stations,
                                       through=through, parts=parts, **kwargs)

    def test_scoped_issue_validation_and_replay(self):
        scoped_batches(self)
        with self.assertRaises(WarehouseError):
            issue_scoped_delivery(self.root, 'bad', day=DAY, branch=1, stations=[], after=0,
                                  through=1, mode='advance')
        with self.assertRaises(WarehouseError):
            issue_scoped_delivery(self.root, 'bad', day=DAY, branch=1, stations=[NORTH, NORTH],
                                  after=0, through=1, mode='advance')
        with self.assertRaises(WarehouseError):
            issue_scoped_delivery(self.root, 'bad', day=DAY, branch=1, stations=[NORTH], after=1,
                                  through=1, mode='advance')
        with self.assertRaises(WarehouseError):
            issue_scoped_delivery(self.root, 'bad', day=DAY, branch=1, stations=[NORTH], after=0,
                                  through=1, mode='watch')
        pending = issue_scoped_delivery(self.root, 'future', day=DAY, branch=1, stations=[NORTH],
                                        after=0, through=9, mode='advance')
        self.assertEqual((pending['status'], pending['missing'], pending['package']),
                         ('pending', ['formal_range'], None))
        with self.assertRaises(Conflict):
            issue_scoped_delivery(self.root, 'future', day=DAY, branch=1, stations=[NORTH],
                                  after=0, through=8, mode='advance')
        sealed = self.issue_scoped('north-two', [NORTH], 0, 2, 'advance')
        again = issue_scoped_delivery(self.root, 'north-two', day=DAY, branch=1,
                                      stations=(NORTH,), after=0, through=2, mode='advance')
        self.assertEqual(again['package'], sealed)
        self.assertEqual(read_issue_result(self.root, 'north-two'), again)
        pieces = json.loads(sealed)['pieces']
        self.assertEqual(set(pieces), {'1', '2'})
        self.assertEqual([entry['row'] for entry in pieces['1']['rows']], [1])
        self.assertEqual({entry['change']['station'] for entry in pieces['1']['rows']}, {NORTH})

    def test_missing_piece_is_not_an_empty_batch(self):
        scoped_batches(self)
        first = self.issue_scoped('north-two', [NORTH], 0, 2, 'advance')
        with self.replica() as replica:
            waiting = self.receive_scoped(replica, 'start', [NORTH], 2, (subset(first, {'1'}),))
            self.assertEqual((waiting['status'], waiting['missing']), ('pending', ['2']))
            self.assertEqual(waiting['boundary'], {'branch': 1, 'through': 0})
            self.assertEqual(lookup_daily(replica, DAY)['total_liters'], 0)
            self.assertIsNone(read_receipt(replica, 'start'))
        with self.replica() as replica:
            done = self.receive_scoped(replica, 'start', [NORTH], 2, (subset(first, {'2'}),))
            self.assertEqual(done['status'], 'published')
            report = lookup_fixed_report(replica, done['report_id'])
            self.assertEqual((report['covered_stations'], report['total_liters']), ([NORTH], 11))
            self.assertEqual(read_receipt(replica, 'start'), done)

    def test_expansion_joint_publication_and_conflicts(self):
        scoped_batches(self)
        first = self.issue_scoped('north-start', [NORTH], 0, 2, 'advance')
        tail = self.issue_scoped('north-tail', [NORTH], 1, 4, 'advance')
        opening = self.issue_scoped('south-open', [SOUTH], 2, 4, 'opening')
        later_opening = self.issue_scoped('south-later', [SOUTH], 2, 5, 'opening')
        both = sorted((NORTH, SOUTH))
        with self.replica() as replica:
            start = self.receive_scoped(replica, 'start', [NORTH], 2, (first,))
            old = lookup_fixed_report(replica, start['report_id'])
            with self.assertRaises(Conflict):
                self.receive_scoped(replica, 'expand', both, 4, (tail, later_opening))
            waiting = self.receive_scoped(replica, 'expand', both, 4,
                                          (subset(tail, {'4'}), opening))
            self.assertEqual((waiting['status'], waiting['missing']), ('pending', ['3']))
            self.assertEqual(lookup_daily(replica, DAY), old)
            result = self.receive_scoped(replica, 'expand', both, 4, (subset(tail, {'3'}),))
            self.assertEqual(result['status'], 'published')
            self.assertEqual(self.receive_scoped(replica, 'expand', both, 4, (tail, opening)),
                             result)
            report = lookup_fixed_report(replica, result['report_id'])
            self.assertEqual(report['covered_stations'], both)
            self.assertEqual(report['total_liters'], 36)
            self.assertEqual([(line['station'], line['liters'], line['provenance']['batch'],
                               line['provenance']['row']) for line in report['lines']],
                             [(NORTH, 19, 4, 2), (SOUTH, 17, 4, 1)])
            self.assertEqual(lookup_fixed_report(replica, start['report_id']), old)
            with self.assertRaises(Conflict):
                self.receive_scoped(replica, 'shrink', [NORTH], 5)
            with self.assertRaises(Conflict):
                self.receive_scoped(replica, 're-expand', [NORTH, SOUTH, '东闸'], 5)
            with self.assertRaises(Conflict):
                receive_scoped_delivery(replica, 'other-day', day='2026-06-19', branch=1,
                                        stations=both, through=5)
            with self.assertRaises(Conflict):
                receive_scoped_delivery(replica, 'other-branch', day=DAY, branch=2, stations=both,
                                        through=5)

    def test_digest_and_preimage_conflicts_stay_unpublished(self):
        scoped_batches(self)
        first = self.issue_scoped('north-start', [NORTH], 0, 2, 'advance')
        tail = self.issue_scoped('north-tail', [NORTH], 1, 4, 'advance')
        opening = self.issue_scoped('south-open', [SOUTH], 2, 4, 'opening')
        both = sorted((NORTH, SOUTH))
        with self.replica() as replica:
            self.receive_scoped(replica, 'start', [NORTH], 2, (first,))
            packet = json.loads(tail)
            packet['pieces']['4']['rows'][0]['change']['before'] = 10
            forged = json.dumps(packet, ensure_ascii=False).encode('utf-8')
            with self.assertRaises(Conflict):
                self.receive_scoped(replica, 'expand', both, 4, (forged, opening))
            from warehouse.export.packages import package_fingerprint
            packet['declaration']['sha256']['4'] = package_fingerprint(packet['pieces']['4'])
            forged = json.dumps(packet, ensure_ascii=False).encode('utf-8')
            with self.assertRaises(Conflict):
                self.receive_scoped(replica, 'expand', both, 4, (forged, opening))
            self.assertIsNone(read_receipt(replica, 'expand'))
            self.assertEqual(read_progress(replica)['boundary'], {'branch': 1, 'through': 2})
            self.assertEqual(lookup_daily(replica, DAY)['total_liters'], 11)

    def test_scoped_and_whole_replicas_do_not_mix(self):
        scoped_batches(self)
        whole = self.issue('whole', 0, 2)
        first = self.issue_scoped('north-start', [NORTH], 0, 2, 'advance')
        with self.replica('scoped') as scoped, self.replica('whole') as whole_replica:
            self.receive_scoped(scoped, 'start', [NORTH], 2, (first,))
            with self.assertRaises(Conflict):
                self.receive(scoped, 'whole', whole)
            self.receive(whole_replica, 'whole', whole)
            with self.assertRaises(Conflict):
                self.receive_scoped(whole_replica, 'start', [NORTH], 2, (first,))
