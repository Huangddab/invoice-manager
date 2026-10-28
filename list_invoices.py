from decimal import Decimal
from database import init_db,list_invoices

init_db()
invoices = list_invoices()

for invoice in invoices:
    amount = Decimal(invoice["total_amount_fen"]) / 100
    printed = "已打印" if invoice["is_printed"] else "未打印"

    print(
        f"{invoice['invoice_number']} | "
        f"{invoice['seller']} | "
        f"{amount:.2f} 元 | "
        f"{printed} | "
        f"{invoice['status']}"
    )

print(f"数据库中共有 {len(invoices)} 张发票")