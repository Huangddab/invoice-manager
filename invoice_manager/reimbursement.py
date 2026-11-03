"""Create a reserved reimbursement in one user action; database rechecks inventory."""
from invoice_manager import database
from invoice_manager.invoice_selector import select_invoice_combination


def create_batch(month, days, allowance, other_numbers):
    inventory = database.list_invoices()
    meals = select_invoice_combination(inventory, days * allowance) if days * allowance else []
    if days * allowance and not meals:
        raise ValueError('暂无可用餐补发票；只报其他发票时，将天数设为 0。')
    others = [row for row in inventory if row['invoice_number'] in other_numbers
              and row['category'] == 'other' and row['status'] == 'available']
    if {row['invoice_number'] for row in others} != set(other_numbers):
        raise ValueError('所选其他发票已发生变化，请重新选择。')
    rows = meals + others
    if not rows:
        raise ValueError('暂无可用发票，请到「发票库存」导入。')
    return database.create_reimbursement_batch(
        month, days, allowance, [row['invoice_number'] for row in rows],
        expected_categories={row['invoice_number']: row['category'] for row in rows})
