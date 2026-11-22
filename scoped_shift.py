import argparse
import json
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from warehouse.export.issues import issue_delivery
from warehouse.recovery.workspace import open_active_workspace
from warehouse.replica.intake import read_receipt, receive_delivery
from warehouse.replica.progress import open_replica, read_progress
from warehouse.report.lookup import lookup_daily, lookup_fixed_report
from warehouse.source.batches import submit_batch
from warehouse.source.validation import Conflict


EXAMPLE_PATH = Path(__file__).with_name("examples") / "scoped_shift.json"


class InterruptedPublication(Exception):
    pass


def check(actual, expected):
    if actual != expected:
        raise AssertionError(f"expected {expected!r}, got {actual!r}")


def seed(source_root, example):
    with open_active_workspace(source_root) as source:
        for number, rows in enumerate(example["batches"], 1):
            receipt = submit_batch(source, f"scoped-example-{number}", rows)
            check(receipt["number"], number)


def baseline(base, source_root, example):
    day, branch = example["day"], example["branch"]
    issued = [issue_delivery(source_root, f"v1-{end}", day=day, branch=branch,
                             after=start, through=end)
              for start, end in ((0, 2), (2, 4), (4, 5))]
    for result in issued:
        check(result["status"], "sealed")
    source_root.rename(base / "unmounted")
    with closing(open_replica(base / "whole.sqlite3")) as replica:
        for end, liters, result in zip((2, 4, 5), (16, 36, 42), issued):
            receipt = receive_delivery(replica, f"v1-{end}", result["package"])
            report = lookup_fixed_report(replica, receipt["report_id"])
            check(report["boundary"], {"branch": branch, "through": end})
            check(report["total_liters"], liters)
            print(json.dumps(report, ensure_ascii=False))


def subset(encoded, numbers):
    packet = json.loads(encoded)
    packet["pieces"] = {key: value for key, value in packet["pieces"].items() if key in numbers}
    return json.dumps(packet, ensure_ascii=False).encode("utf-8")


def scoped(base, source_root, example):
    from warehouse.export.issues import issue_scoped_delivery
    from warehouse.replica.intake import receive_scoped_delivery

    day, branch = example["day"], example["branch"]
    north, south = example["stations"]
    both = sorted((north, south))
    path = base / "scoped.sqlite3"

    def issue(name, stations, after, through, mode):
        result = issue_scoped_delivery(source_root, name, day=day, branch=branch, stations=stations,
                                       after=after, through=through, mode=mode)
        check(result["status"], "sealed")
        return result["package"]

    def receive(replica, name, stations, through, parts=(), **kwargs):
        return receive_scoped_delivery(replica, name, day=day, branch=branch, stations=stations,
                                       through=through, parts=parts, **kwargs)

    first = issue("north-start", [north], 0, 2, "advance")
    tail = issue("north-tail", [north], 1, 4, "advance")
    opening = issue("south-at-four", [south], 2, 4, "opening")
    later = issue("south-at-five", [south], 2, 5, "opening")
    check(issue("north-tail", [north], 1, 4, "advance"), tail)
    tail_pieces = json.loads(tail)["pieces"]
    check(set(tail_pieces), {"2", "3", "4"})
    check(tail_pieces["3"]["rows"], [])
    for piece in tail_pieces.values():
        check({entry["change"]["station"] for entry in piece["rows"]} - {north}, set())
    check([(entry["row"], entry["change"]["key"]) for entry in tail_pieces["4"]["rows"]], [(2, "A")])
    source_root.rename(base / "unmounted")

    with closing(open_replica(path)) as replica:
        start = receive(replica, "start", [north], 2, (first,))
        check(start["status"], "published")
        old = lookup_fixed_report(replica, start["report_id"])
        check((old["covered_stations"], old["total_liters"]), ([north], 11))
        try:
            receive(replica, "expand", both, 4, (tail, later))
        except Conflict:
            pass
        else:
            raise AssertionError("a later opening was accepted for an earlier cutoff")
        check(lookup_daily(replica, day), old)
        waiting = receive(replica, "expand", both, 4, (subset(tail, {"4"}), opening))
        check((waiting["status"], waiting["boundary"]), ("pending", old["boundary"]))
        check(read_receipt(replica, "expand"), None)
        check(lookup_daily(replica, day), old)
        print(json.dumps({"previous": old, "waiting": waiting}, ensure_ascii=False))

    with closing(open_replica(path)) as replica, closing(open_replica(path)) as reader:
        before_commit = []

        def interrupt(stage):
            if stage == "before_commit":
                check(read_progress(reader)["boundary"], old["boundary"])
                check(lookup_daily(reader, day), old)
                check(read_receipt(reader, "expand"), None)
                before_commit.append(stage)
                raise InterruptedPublication("before commit")

        try:
            receive(replica, "expand", both, 4, (subset(tail, {"3"}),), on_stage=interrupt)
        except InterruptedPublication:
            pass
        else:
            raise AssertionError("the joint publication did not reach its commit callback")
        check(before_commit, ["before_commit"])
        check(lookup_daily(reader, day), old)
        check(read_receipt(reader, "expand"), None)

    with closing(open_replica(path)) as replica:
        result = receive(replica, "expand", both, 4)
        check(result["status"], "published")
        check(receive(replica, "expand", both, 4, (tail, opening)), result)
        check(read_receipt(replica, "expand"), result)
        report = lookup_fixed_report(replica, result["report_id"])
        check(lookup_daily(replica, day), report)
        check(lookup_fixed_report(replica, start["report_id"]), old)
        check(report["boundary"], {"branch": branch, "through": 4})
        check(report["covered_stations"], both)
        check(report["total_liters"], 36)
        check(report["intake_receipt"], result)
        check([(line["station"], line["liters"], line["provenance"]["batch"], line["provenance"]["row"])
               for line in report["lines"]], [(north, 19, 4, 2), (south, 17, 4, 1)])
        print(json.dumps({"published": report, "historical": old}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="Exercise an offline watershed shift handoff.")
    parser.add_argument("mode", choices=("baseline", "verify", "demo"))
    mode = parser.parse_args().mode
    if mode == "verify":
        subprocess.run([sys.executable, "-B", "check_project.py"], check=True)
    example = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
    with TemporaryDirectory() as directory:
        base = Path(directory)
        source_root = base / "source"
        seed(source_root, example)
        if mode == "baseline":
            baseline(base, source_root, example)
        else:
            scoped(base, source_root, example)


if __name__ == "__main__":
    main()
