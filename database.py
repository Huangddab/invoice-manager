import sqlite3

from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "invoices.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS invoices (
                invoice_number TEXT PRIMARY KEY,
                total_amount_fen INTEGER NOT NULL,
                invoice_date TEXT NOT NULL,
                buyer TEXT NOT NULL,
                seller TEXT NOT NULL,
                file_path TEXT NOT NULL,
                is_printed INTEGER NOT NULL DEFAULT 0
                    CHECK (is_printed IN (0, 1)),
                status TEXT NOT NULL DEFAULT 'available'
                    CHECK (status IN ('available', 'reserved', 'used', 'unusable')),
                remark TEXT NOT NULL DEFAULT ''
            )
        """)
        conn.commit()
        
    finally:
        conn.close()

def save_invoice(invoice):
    required_fields = (
        "invoice_number",
        "total_amount",
        "invoice_date",
        "buyer",
        "seller",
        "file_path",
    )
    for field in required_fields:
        if invoice.get(field) is None or invoice.get(field) == "":
            raise ValueError(f"发票缺少必要字段：{field}")

    amount_fen = invoice["total_amount"] * 100

    if amount_fen != amount_fen.to_integral_value():
        raise ValueError("发票金额不能超过两位小数")

    conn = sqlite3.connect(DB_PATH)

    try:
        cursor = conn.execute("""
            INSERT INTO invoices (
                invoice_number,
                total_amount_fen,
                invoice_date,
                buyer,
                seller,
                file_path
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(invoice_number) DO NOTHING
        """, (
            invoice["invoice_number"],
            int(amount_fen),
            invoice["invoice_date"].isoformat(),
            invoice["buyer"],
            invoice["seller"],
            invoice["file_path"],
        ))

        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()

        
if __name__ == "__main__":
    init_db()
    print("数据库初始化完成:",DB_PATH)