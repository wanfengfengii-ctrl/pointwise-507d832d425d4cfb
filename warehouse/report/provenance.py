from typing import TypedDict


class RowProvenance(TypedDict):
    branch: int
    batch: int
    row: int
    source_receipt: dict


def build_row_provenance(batch, row_number) -> RowProvenance:
    return {'branch': batch['branch'], 'batch': batch['number'], 'row': row_number,
            'source_receipt': dict(batch['receipt'])}
