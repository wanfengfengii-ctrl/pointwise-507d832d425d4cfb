import json
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from warehouse.export.issues import issue_delivery
from warehouse.export.packages import decode_package, encode_package
from warehouse.recovery.checkpoints import capture_checkpoint
from warehouse.recovery.restore import restore_source
from warehouse.recovery.workspace import open_active_workspace
from warehouse.replica.intake import read_receipt, receive_delivery
from warehouse.replica.progress import open_replica
from warehouse.report.lookup import lookup_daily, lookup_fixed_report
from warehouse.source.batches import submit_batch
from warehouse.source.validation import Conflict


def run(root, sample):
    day = sample['day']

    def submit(request, rows):
        with open_active_workspace(root) as source:
            return submit_batch(source, request, [dict(row, day=day) for row in rows])

    def issue(request, after, through, branch=1, restore_id=None):
        return issue_delivery(root, request, day=day, after=after, through=through,
                              branch=branch, restore_id=restore_id)['package']

    opening_receipt = submit('opening', sample['opening'])
    opening_report = lookup_daily(root, day, freeze=True)
    checkpoint = capture_checkpoint(root, 'opening-backup', 1, 1)
    opening = issue('opening-range', 0, 1)
    for index, row in enumerate(sample['old_corrections'], 1):
        submit(f'old-correction-{index}', [row])
    corrections = issue('old-correction-range', 1, 3)
    submit('late-correction', [sample['late_correction']])
    late = issue('late-range', 3, 4)
    restored = restore_source(root, 'recover-shift', 'opening-backup')
    restored_batch = submit('restored-correction', [sample['restored_correction']])
    handoff = issue('handoff', 1, 2, restored['branch'], 'recover-shift')
    package = decode_package(handoff)
    partial = encode_package(replace(package, basis=None))

    ahead_path, behind_path = root / 'night-ahead.sqlite3', root / 'night-behind.sqlite3'
    with closing(open_replica(ahead_path)) as ahead, closing(open_replica(behind_path)) as behind:
        receive_delivery(ahead, 'opening', opening)
        old = receive_delivery(ahead, 'old-corrections', corrections)
        pending = receive_delivery(behind, 'switch', partial)
        assert pending['status'] == 'pending' and pending['missing'] == ['restore_basis']
        assert read_receipt(behind, 'switch') is None
        assert lookup_daily(behind, day)['total_liters'] == 0
        ready = receive_delivery(ahead, 'switch', handoff)
        assert lookup_daily(ahead, day)['total_liters'] == 28
        try:
            receive_delivery(ahead, 'late', late)
        except Conflict:
            assert lookup_daily(ahead, day)['intake_receipt'] == ready
        else:
            raise AssertionError('late old delivery changed its predecessor branch')
    with closing(open_replica(ahead_path)) as ahead, closing(open_replica(behind_path)) as behind:
        behind_ready = receive_delivery(behind, 'switch', handoff)
        ahead_report = lookup_fixed_report(ahead, ready['report_id'])
        behind_report = lookup_fixed_report(behind, behind_ready['report_id'])
        original_report = lookup_fixed_report(ahead, old['report_id'])
        replay = receive_delivery(ahead, 'switch', handoff)
        assert replay == ready
        assert (opening_report['total_liters'], original_report['total_liters'],
                ahead_report['total_liters'], behind_report['total_liters']) == (18, 24, 28, 28)
        assert ahead_report['boundary'] == behind_report['boundary'] == {'branch': 2, 'through': 2}
        assert ahead_report['missing'] == behind_report['missing'] == 1
        assert ahead_report['lines'][0]['provenance']['source_receipt'] == restored_batch
        assert behind_report['lines'][1]['provenance']['source_receipt'] == opening_receipt
        return {'day': day, 'checkpoint': checkpoint, 'opening_liters': opening_report['total_liters'],
                'old_fixed_liters': original_report['total_liters'], 'pending': pending,
                'ahead_liters': ahead_report['total_liters'], 'behind_liters': behind_report['total_liters'],
                'missing': ahead_report['missing'], 'current_boundary': ahead_report['boundary'],
                'restored_row_origin': ahead_report['lines'][0]['provenance'], 'replayed_receipt': replay}


def main():
    sample_path = Path(__file__).resolve().parent / 'examples' / 'shift.json'
    sample = json.loads(sample_path.read_text(encoding='utf-8'))
    with TemporaryDirectory(prefix='chengxi-demo-') as directory:
        result = run(Path(directory), sample)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
