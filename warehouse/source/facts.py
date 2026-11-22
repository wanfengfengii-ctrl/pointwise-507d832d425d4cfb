import json

from warehouse.report.provenance import build_row_provenance
from warehouse.source.store import json_text
from warehouse.source.validation import ABSENT_OBSERVATION, observation_key, validate_before_value


def read_facts(source, day=None):
    if day is None:
        rows = source.execute('SELECT fact FROM source_facts ORDER BY day, station, observation')
    else:
        rows = source.execute('SELECT fact FROM source_facts WHERE day = ? ORDER BY station, observation',
                              (day,))
    return [json.loads(row['fact']) for row in rows]


def fact_after(previous, change, batch, row_number):
    key = observation_key(change)
    actual = ABSENT_OBSERVATION if previous is None else previous['liters']
    validate_before_value(actual, change['before'], (*key, batch['branch'], batch['number'], row_number))
    return {
        'day': change['day'], 'station': change['station'], 'key': change['key'],
        'before': change['before'], 'liters': change['after'],
        'provenance': build_row_provenance(batch, row_number),
    }


def apply_fact_changes(source, batch):
    for position, change in enumerate(batch['changes'], 1):
        key = observation_key(change)
        row = source.execute('''SELECT fact FROM source_facts
                                WHERE day = ? AND station = ? AND observation = ?''', key).fetchone()
        previous = None if row is None else json.loads(row['fact'])
        fact = fact_after(previous, change, batch, position)
        source.execute('''INSERT INTO source_facts VALUES (?, ?, ?, ?)
                          ON CONFLICT (day, station, observation) DO UPDATE SET fact = excluded.fact''',
                       (*key, json_text(fact)))


def project_facts(initial_facts, batches):
    facts = {observation_key(fact): fact for fact in initial_facts}
    for batch in batches:
        for position, change in enumerate(batch['changes'], 1):
            key = observation_key(change)
            facts[key] = fact_after(facts.get(key), change, batch, position)
    return [facts[key] for key in sorted(facts)]
