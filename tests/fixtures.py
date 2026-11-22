import json
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from warehouse.export.issues import issue_delivery
from warehouse.export.packages import decode_package, encode_package
from warehouse.recovery.checkpoints import capture_checkpoint
from warehouse.recovery.restore import restore_source
from warehouse.recovery.workspace import open_active_workspace
from warehouse.replica.intake import receive_delivery
from warehouse.replica.progress import open_replica
from warehouse.source.batches import submit_batch
from warehouse.source.validation import ABSENT_OBSERVATION


DAY = '2026-06-18'
NORTH = '北闸'
SOUTH = '南闸'


def change(key, before, after, *, day=DAY, station=NORTH):
    return {'day': day, 'station': station, 'key': key, 'before': before, 'after': after}


def original_rows():
    return [change('B', ABSENT_OBSERVATION, 11), change('C', ABSENT_OBSERVATION, None),
            change('A', ABSENT_OBSERVATION, 7)]


def scenario(case_id, inputs, expected, actual, downstream_expected, downstream_actual, code_paths, oracle):
    value = {'case_id': case_id, 'input': inputs, 'expected': expected, 'actual': actual,
             'downstream_expected': downstream_expected, 'downstream_actual': downstream_actual,
             'code_paths': code_paths, 'oracle': oracle}
    print('POINTWISE_SCENARIO ' + json.dumps(value, ensure_ascii=True, sort_keys=True))


class WarehouseCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))

    def replica(self, name='night'):
        return closing(open_replica(self.root / (name + '.sqlite3')))

    def submit(self, name, *changes):
        with open_active_workspace(self.root) as source:
            return submit_batch(source, name, changes)

    def issue(self, issue_id, after, through, *, branch=1, restore_id=None, day=DAY):
        issued = issue_delivery(self.root, issue_id, day=day, branch=branch, after=after, through=through,
                                restore_id=restore_id)
        self.assertEqual(issued['status'], 'sealed')
        return decode_package(issued['package'])

    def receive(self, replica, name, package, **kwargs):
        return receive_delivery(replica, name, encode_package(package), **kwargs)

    def recovery_case(self, new_value=17, *, missing=False):
        self.submit('initial', change('K', ABSENT_OBSERVATION, 5))
        for number in range(2, 10):
            self.submit(f'confirm-{number}', change('K', 5, 5))
        behind = self.issue('old-prefix', 0, 9)
        rows = [change('K', 5, 7)]
        if missing:
            rows.append(change('M', ABSENT_OBSERVATION, None))
        self.submit('checkpoint-reading', *rows)
        capture_checkpoint(self.root, 'before-tail', 1, 10)
        old_receipt = self.submit('old-eleven', change('K', 7, 11))
        ahead = self.issue('old-through-eleven', 0, 11)
        self.submit('old-twelve', change('K', 11, 13))
        late = self.issue('old-late', 11, 12)
        recovery = restore_source(self.root, 'restore-shift', 'before-tail')
        new_receipt = self.submit('new-eleven', change('K', 7, new_value))
        handoff = self.issue('new-shift', 10, 11, branch=recovery['branch'], restore_id='restore-shift')
        return {'behind': behind, 'ahead': ahead, 'late': late, 'handoff': handoff,
                'old_receipt': old_receipt, 'new_receipt': new_receipt, 'recovery': recovery}
