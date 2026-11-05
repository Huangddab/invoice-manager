def _dense_choice(amounts, target):
    """Find the best sum/count with bitsets, then recover the earliest subset."""
    count = len(amounts)
    reachable = [1] + [0] * count
    for index, amount in enumerate(amounts):
        for size in range(index + 1, 0, -1):
            reachable[size] |= reachable[size - 1] << amount

    lower_mask = (1 << target) - 1
    best_distance = None
    best_count = None
    best_sums = []
    for size in range(1, count + 1):
        bits = reachable[size]
        lower = bits & lower_mask
        upper = bits >> target
        sums = []
        if lower:
            sums.append(lower.bit_length() - 1)
        if upper:
            sums.append(target + (upper & -upper).bit_length() - 1)
        for total in sums:
            distance = abs(total - target)
            if best_distance is None or (distance, size) < (best_distance, best_count):
                best_distance, best_count, best_sums = distance, size, [total]
            elif (distance, size) == (best_distance, best_count) and total not in best_sums:
                best_sums.append(total)
        if best_distance == 0:
            break

    estimated_suffix_bytes = (count + 1) * (best_count + 1) * ((max(best_sums) + 8) // 8)
    if estimated_suffix_bytes > 64_000_000:
        return None
    limit = (1 << (max(best_sums) + 1)) - 1
    suffix = [None] * (count + 1)
    suffix[count] = [1] + [0] * best_count
    for index in range(count - 1, -1, -1):
        following = suffix[index + 1]
        current = following.copy()
        for size in range(1, min(best_count, count - index) + 1):
            current[size] |= (following[size - 1] << amounts[index]) & limit
        suffix[index] = current

    def recover(total):
        remaining, size = total, best_count
        chosen = []
        for index, amount in enumerate(amounts):
            if (size and remaining >= amount
                    and (suffix[index + 1][size - 1] >> (remaining - amount)) & 1):
                chosen.append(index)
                remaining -= amount
                size -= 1
        return tuple(chosen)

    return min(recover(total) for total in best_sums)


def select_invoice_combination(invoices, target_fen, buyer=None):
    if type(target_fen) is not int or target_fen <= 0:
        raise ValueError("报销目标必须是正整数，单位为分")

    candidates = sorted(
        (
            invoice
            for invoice in invoices
            if invoice["status"] == "available"
            and invoice.get("category", "meal") == "meal"
            and (buyer is None or invoice["buyer"] == buyer)
            and invoice["total_amount_fen"] > 0
        ),
        key=lambda invoice: (invoice["invoice_date"], invoice["invoice_number"]),
    )

    if not candidates:
        return []

    amounts = [invoice['total_amount_fen'] for invoice in candidates]
    total_amount = sum(amounts)
    if total_amount < target_fen:
        return candidates
    # Bitsets are fast for a large but bounded inventory; sparse sums avoid
    # allocating huge integers for unusually large invoice amounts.
    if 40 <= len(candidates) <= 300 and total_amount <= 1_000_000:
        indexes = _dense_choice(amounts, target_fen)
        if indexes is not None:
            return [candidates[index] for index in indexes]

    # Only sums below the target can be extended. Completed sums need only
    # their best choice, so large inventories do not retain every overshoot.
    combinations = {0: ()}
    completed = None
    for index, invoice in enumerate(candidates):
        amount = invoice["total_amount_fen"]
        for subtotal, indexes in list(combinations.items()):
            total = subtotal + amount
            choice = indexes + (index,)
            if total >= target_fen:
                if completed is None or (total - target_fen, len(choice), choice) < (
                    completed[0] - target_fen, len(completed[1]), completed[1]
                ):
                    completed = (total, choice)
                continue
            previous = combinations.get(total)
            if previous is None or (len(choice), choice) < (len(previous), previous):
                combinations[total] = choice

    choices = [(total, indexes) for total, indexes in combinations.items() if total > 0]
    if completed is not None:
        choices.append(completed)
    _, indexes = min(choices, key=lambda item: (
        abs(item[0] - target_fen), len(item[1]), item[1],
    ))
    return [candidates[index] for index in indexes]
