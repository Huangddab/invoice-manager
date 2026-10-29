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

# 保存发票信息
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

# 列出发票列表
def list_invoices():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute("""
            SELECT
                invoice_number,
                total_amount_fen,
                invoice_date,
                seller,
                file_path,
                is_printed,
                status,
                remark
            FROM invoices
            ORDER BY invoice_date DESC, invoice_number
        """)
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()

# 更新发票备注
def update_remark(invoice_number,remark):
    conn = sqlite3.connect(DB_PATH)

    try:
        cursor = conn.execute("""
            UPDATE invoices
            SET remark = ?
            WHERE invoice_number = ?
        """, (remark, invoice_number))

        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()

# 更新发票打印状态
def update_printed(invoice_number,is_printed):
    if type(is_printed) is not bool:
        raise ValueError("打印状态必须是True或False")

    conn  =sqlite3.connect(DB_PATH)

    try:
        cursor = conn.execute("""
            UPDATE invoices
            SET is_printed = ?
            WHERE invoice_number = ?
        """, (int(is_printed), invoice_number))

        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()

# 批量更新发票打印状态
def update_printed_batch(invoice_numbers, is_printed):
    if type(is_printed) is not bool:
        raise ValueError("打印状态必须是True或False")

    conn = sqlite3.connect(DB_PATH)

    try:
        cursor = conn.executemany("""
            UPDATE invoices
            SET is_printed = ?
            WHERE invoice_number = ?
        """, [(int(is_printed), invoice_number) for invoice_number in invoice_numbers])

        if cursor.rowcount != len(invoice_numbers):
            raise ValueError("部分发票不存在，请刷新后重试")
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

if __name__ == "__main__":
    init_db()
    print("数据库初始化完成:",DB_PATH)


def update_status_batch(invoice_numbers, status):
    if status not in ("available", "reserved", "used", "unusable"):
        raise ValueError("无效的使用状态")
    numbers = list(dict.fromkeys(invoice_numbers))
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.executemany(
            "UPDATE invoices SET status = ? WHERE invoice_number = ?",
            [(status, number) for number in numbers],
        )
        if cursor.rowcount != len(numbers):
            raise ValueError("部分发票不存在，请刷新后重试")
    return True
