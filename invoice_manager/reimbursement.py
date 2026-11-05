"""Create a reserved reimbursement in one user action; database rechecks inventory."""
from invoice_manager import database
from invoice_manager.invoice_selector import select_invoice_combination


def create_batch(month, days, allowance, other_numbers, buyer, manual_target_fen=None):
    if not buyer:
        raise ValueError('请先选择购买方公司。')
    inventory = database.list_invoices()
    meal_target = manual_target_fen if manual_target_fen is not None else days * allowance
    meals = select_invoice_combination(inventory, meal_target, buyer=buyer) if meal_target else []
    if meal_target and not meals:
        raise ValueError('暂无可用餐补发票；只报其他发票时，将天数设为 0。')
    others = [row for row in inventory if row['invoice_number'] in other_numbers
              and row['category'] == 'other' and row['status'] == 'available' and row['buyer'] == buyer]
    if {row['invoice_number'] for row in others} != set(other_numbers):
        raise ValueError('所选其他发票已发生变化，请重新选择。')
    rows = meals + others
    if not rows:
        raise ValueError('暂无可用发票，请到「发票库存」导入。')
    return database.create_reimbursement_batch(
        month, days, allowance, [row['invoice_number'] for row in rows],
        expected_categories={row['invoice_number']: row['category'] for row in rows}, expected_buyer=buyer,
        manual_target_fen=manual_target_fen)
