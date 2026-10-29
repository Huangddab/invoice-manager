from invoice_selector import select_invoice_combination


invoices = [
    {
        "invoice_number": "A",
        "total_amount_fen": 42800,
        "invoice_date": "2026-10-01",
        "status": "available",
    },
    {
        "invoice_number": "B",
        "total_amount_fen": 23200,
        "invoice_date": "2026-10-02",
        "status": "available",
    },
    {
        "invoice_number": "C",
        "total_amount_fen": 26800,
        "invoice_date": "2026-10-03",
        "status": "available",
    },
]

selected = select_invoice_combination(invoices, 50000)

for invoice in selected:
    print(invoice["invoice_number"], invoice["total_amount_fen"])

print(
    "合计（分）：",
    sum(invoice["total_amount_fen"] for invoice in selected),
)