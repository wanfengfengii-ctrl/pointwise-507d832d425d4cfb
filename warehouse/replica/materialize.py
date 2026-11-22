from dataclasses import dataclass

from warehouse.source.facts import project_facts


@dataclass(frozen=True)
class CandidateFacts:
    facts: list


def apply_formal_range(initial_facts, batches):
    return CandidateFacts(project_facts(initial_facts, batches))
