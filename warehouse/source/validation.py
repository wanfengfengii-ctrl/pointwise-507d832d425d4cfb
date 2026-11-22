from datetime import date


ABSENT_OBSERVATION = 'absent'
SQLITE_MAX_POSITION = (1 << 63) - 1


class WarehouseError(ValueError):
    pass


class Conflict(WarehouseError):
    pass


class NotFound(WarehouseError):
    pass


def require_name(value, label):
    if not isinstance(value, str) or not value.strip():
        raise WarehouseError(f'{label} requires a nonempty name')
    return value


def require_day(value):
    require_name(value, 'day')
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise WarehouseError('day requires YYYY-MM-DD') from exc
    if parsed.isoformat() != value:
        raise WarehouseError('day requires YYYY-MM-DD')
    return value


def require_position(value, label, *, minimum=0):
    if type(value) is not int or not minimum <= value <= SQLITE_MAX_POSITION:
        raise WarehouseError(f'{label} requires an integer position of at least {minimum}')
    return value


def require_station_set(value):
    if not isinstance(value, (list, tuple)) or not value:
        raise WarehouseError('stations requires a nonempty list or tuple of names')
    stations = [require_name(station, 'station') for station in value]
    if len(set(stations)) != len(stations):
        raise WarehouseError('stations requires distinct names')
    return sorted(stations)


def require_fields(value, fields, label):
    if not isinstance(value, dict) or value.keys() != set(fields):
        raise WarehouseError(f'{label} requires fields {", ".join(sorted(fields))}')
    return value


def require_reading(value, label, *, allow_absent=False):
    if value is None or type(value) is int:
        return value
    if allow_absent and value == ABSENT_OBSERVATION:
        return value
    raise WarehouseError(f'invalid {label} reading')


def observation_key(row):
    return row['day'], row['station'], row['key']


def validate_batch(changes):
    if not isinstance(changes, (list, tuple)) or not changes:
        raise WarehouseError('a formal batch requires at least one ordered row')
    validated = []
    for row in changes:
        require_fields(row, ('day', 'station', 'key', 'before', 'after'), 'change')
        require_day(row['day'])
        require_name(row['station'], 'station')
        require_name(row['key'], 'observation key')
        require_reading(row['before'], 'before', allow_absent=True)
        require_reading(row['after'], 'after')
        validated.append(dict(row))
    return validated


def validate_before_value(actual, expected, coordinates):
    if type(actual) is not type(expected) or actual != expected:
        raise Conflict(f'preimage conflict for {coordinates!r}: expected {expected!r}, found {actual!r}')
