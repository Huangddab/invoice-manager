
def select_invoice_combination(invoices, target_fen):
    if type(target_fen) is not int or target_fen <= 0:
        raise ValueError("报销目标必须是正整数，单位为分")

    candidates = sorted(
        (
            invoice
            for invoice in invoices
            if invoice["status"] == "available"
            and invoice.get("category", "meal") == "meal"
            and invoice["total_amount_fen"] > 0
        ),
        key=lambda invoice: (
            invoice["invoice_date"],
            invoice["invoice_number"],
        ),
    )

    if not candidates:
        return []

    # 金额合计 -> 对应发票在 candidates 中的下标
    combinations = {0: ()}

    for index, invoice in enumerate(candidates):
        amount = invoice["total_amount_fen"]

        # 使用快照，保证本轮不会重复使用当前发票
        for subtotal, indexes in list(combinations.items()):
            if subtotal >= target_fen:
                continue

            new_total = subtotal + amount
            new_indexes = indexes + (index,)
            previous = combinations.get(new_total)

            if previous is None or (
                len(new_indexes), new_indexes
            ) < (
                len(previous), previous
            ):
                combinations[new_total] = new_indexes

    best_total = min(
        (total for total in combinations if total > 0),
        key=lambda total: (
            abs(total - target_fen),
            len(combinations[total]),
            combinations[total],
        ),
    )

    return [
        candidates[index]
        for index in combinations[best_total]
    ]