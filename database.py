import sqlite3

from pathlib import Path
from datetime import datetime

DB_PATH = Path(__file__).resolve().parent / "invoices.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")

    try:
        # 创建发票表
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
        # 创建报销批次表
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reimbursement_batches (
                id INTEGER PRIMARY KEY,
                reimbursement_month TEXT NOT NULL,
                work_days INTEGER NOT NULL
                    CHECK (work_days BETWEEN 0 AND 31),
                daily_allowance_fen INTEGER NOT NULL
                    CHECK (daily_allowance_fen >= 0),
                status TEXT NOT NULL DEFAULT 'reserved'
                    CHECK (status IN ('reserved', 'used', 'cancelled')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reimbursement_items (
                batch_id INTEGER NOT NULL,
                invoice_number TEXT NOT NULL,
                PRIMARY KEY (batch_id, invoice_number),
                FOREIGN KEY (batch_id)
                    REFERENCES reimbursement_batches(id),
                FOREIGN KEY (invoice_number)
                    REFERENCES invoices(invoice_number)
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
        conn.execute("BEGIN IMMEDIATE")
        for number in numbers:
            linked = conn.execute("""
                SELECT 1 FROM reimbursement_items i
                JOIN reimbursement_batches b ON b.id = i.batch_id
                WHERE i.invoice_number = ? AND b.status != 'cancelled'
            """, (number,)).fetchone()
            if linked:
                raise ValueError("发票已关联报销单，请通过报销记录修改整单状态")
        cursor = conn.executemany(
            "UPDATE invoices SET status = ? WHERE invoice_number = ?",
            [(status, number) for number in numbers],
        )
        if cursor.rowcount != len(numbers):
            raise ValueError("部分发票不存在，请刷新后重试")
    return True

# 获取发票对应的文件路径
def get_invoice_file_path(invoice_number):
    conn = sqlite3.connect(DB_PATH)

    try:
        row = conn.execute("""
            SELECT file_path
            FROM invoices
            WHERE invoice_number = ?
        """, (invoice_number,)).fetchone()

        return row[0] if row else None
    finally:
        conn.close()

# 创建报销批次及其对应的发票项
def create_reimbursement_batch(
    reimbursement_month,
    work_days,
    daily_allowance_fen,
    invoice_numbers,
):
    # 检查月份格式
    try:
        month = datetime.strptime(reimbursement_month, "%Y-%m")
    except ValueError:
        raise ValueError("报销月份格式应为 YYYY-MM")

    if month.strftime("%Y-%m") != reimbursement_month:
        raise ValueError("报销月份格式应为 YYYY-MM")

    if type(work_days) is not int or not 0 <= work_days <= 31:
        raise ValueError("可报销天数必须是 0～31 的整数")

    if (
        type(daily_allowance_fen) is not int
        or daily_allowance_fen < 0
    ):
        raise ValueError("每日标准必须是非负整数，单位为分")

    # 去掉重复号码，同时保留原顺序
    invoice_numbers = list(dict.fromkeys(invoice_numbers))

    if not invoice_numbers:
        raise ValueError("请至少选择一张发票")

    conn = sqlite3.connect(DB_PATH)

    try:
        conn.execute("PRAGMA foreign_keys = ON")
        # 开始创建报销批次及其对应的发票项的事务
        conn.execute("BEGIN IMMEDIATE")
        # 插入报销批次记录
        cursor = conn.execute("""
            INSERT INTO reimbursement_batches (
                reimbursement_month,
                work_days,
                daily_allowance_fen
            )
            VALUES (?, ?, ?)
        """, (
            reimbursement_month,
            work_days,
            daily_allowance_fen,
        ))

        batch_id = cursor.lastrowid
        # 遍历发票号码，更新发票状态并插入报销项记录
        for invoice_number in invoice_numbers:
            cursor = conn.execute("""
                UPDATE invoices
                SET status = 'reserved'
                WHERE invoice_number = ?
                  AND status = 'available'
            """, (invoice_number,))

            if cursor.rowcount != 1:
                raise ValueError(
                    f"发票不存在或不是待使用状态：{invoice_number}"
                )

            conn.execute("""
                INSERT INTO reimbursement_items (
                    batch_id,
                    invoice_number
                )
                VALUES (?, ?)
            """, (batch_id, invoice_number))

        conn.commit()
        return batch_id

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()



def list_reimbursement_batches():
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute("""
            SELECT b.*, COUNT(i.invoice_number) AS invoice_count,
                   COALESCE(SUM(v.total_amount_fen), 0) AS total_amount_fen
            FROM reimbursement_batches b
            LEFT JOIN reimbursement_items i ON i.batch_id = b.id
            LEFT JOIN invoices v ON v.invoice_number = i.invoice_number
            GROUP BY b.id ORDER BY b.id DESC
        """)]


def finish_reimbursement_batch(batch_id, action):
    """Complete or cancel a reserved batch atomically."""
    if action not in ('used', 'cancelled'):
        raise ValueError('无效的报销操作')
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT status FROM reimbursement_batches WHERE id = ?', (batch_id,)).fetchone()
        if not row or row[0] != 'reserved':
            raise ValueError('仅待报销的报销单可以完成或取消，请刷新后重试')
        numbers = [item[0] for item in conn.execute(
            'SELECT invoice_number FROM reimbursement_items WHERE batch_id = ?', (batch_id,))]
        for number in numbers:
            cursor = conn.execute("UPDATE invoices SET status = ? WHERE invoice_number = ? AND status = 'reserved'",
                                  ('used' if action == 'used' else 'available', number))
            if cursor.rowcount != 1:
                raise ValueError('发票状态与报销单不一致，未保存任何修改')
        conn.execute('UPDATE reimbursement_batches SET status = ? WHERE id = ?', (action, batch_id))
    return True
