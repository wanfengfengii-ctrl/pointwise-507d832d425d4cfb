from warehouse.source.batches import read_formal_batches, read_source_limits
from warehouse.source.validation import Conflict


def check_range_coverage(branch, after, through, batches):
    last = after
    contiguous = True
    for batch in batches:
        number = batch['number']
        if batch['branch'] != branch or not last < number <= through:
            raise Conflict('batch is outside its declared branch, scope or order')
        if number != last + 1:
            contiguous = False
        last = number
    return contiguous and last == through


def select_formal_range(source, branch, after, through):
    limits = read_source_limits(source)
    if limits['branch'] != branch or after < limits['base']:
        raise Conflict('start is outside the declared formal branch')
    if through > limits['through']:
        return None
    batches = read_formal_batches(source, branch, after, through)
    if not check_range_coverage(branch, after, through, batches):
        return None
    return batches
