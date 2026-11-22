from dataclasses import dataclass

from warehouse.source.facts import project_facts
from warehouse.source.validation import ABSENT_OBSERVATION, observation_key, validate_before_value


@dataclass(frozen=True)
class CandidateFacts:
    facts: list


def apply_formal_range(initial_facts, batches):
    return CandidateFacts(project_facts(initial_facts, batches))


def apply_scoped_rows(facts, branch, numbered_rows):
    """Apply (batch number, advance row entry) pairs in formal order to a mutable fact map."""
    for number, entry in numbered_rows:
        change = entry['change']
        key = observation_key(change)
        previous = facts.get(key)
        actual = ABSENT_OBSERVATION if previous is None else previous['liters']
        validate_before_value(actual, change['before'], (*key, branch, number, entry['row']))
        facts[key] = {'day': change['day'], 'station': change['station'], 'key': change['key'],
                      'before': change['before'], 'liters': change['after'],
                      'provenance': {'branch': branch, 'batch': number, 'row': entry['row'],
                                     'source_receipt': entry['source_receipt']}}
