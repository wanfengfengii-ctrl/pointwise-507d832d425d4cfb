from copy import deepcopy
from typing import TypedDict

from warehouse.report.provenance import RowProvenance
from warehouse.source.validation import require_day


class DailyLine(TypedDict):
    day: str
    station: str
    key: str
    before: int | str | None
    liters: int | None
    provenance: RowProvenance


class DailyReport(TypedDict):
    report_id: str | None
    day: str
    boundary: dict
    intake_receipt: dict | None
    lines: list[DailyLine]
    stations: list[dict]
    total_liters: int
    missing: int
    measured: int


def build_daily_report(facts, day, boundary, *, report_id=None, intake_receipt=None) -> DailyReport:
    require_day(day)
    lines = sorted((fact for fact in facts if fact['day'] == day),
                   key=lambda fact: (fact['station'], fact['key']))
    stations = {}
    for fact in lines:
        station = fact['station']
        if station not in stations:
            stations[station] = {'station': station, 'liters': 0, 'missing': 0, 'measured': 0}
        tally = stations[station]
        reading = fact['liters']
        if reading is None:
            tally['missing'] += 1
        else:
            tally['liters'] += reading
            tally['measured'] += 1
    subtotals = list(stations.values())
    return {'report_id': report_id, 'day': day, 'boundary': dict(boundary),
            'intake_receipt': deepcopy(intake_receipt), 'lines': deepcopy(lines), 'stations': subtotals,
            'total_liters': sum(row['liters'] for row in subtotals),
            'missing': sum(row['missing'] for row in subtotals),
            'measured': sum(row['measured'] for row in subtotals)}
