import tkinter as tk
from decimal import Decimal
from tkinter import ttk,simpledialog,messagebox

from database import init_db, list_invoices,update_remark

def edit_selected_remark():
    selected = table.selection()
    if len(selected) !=1:
        messagebox.showinfo("提示","请选中一张发票后修改备注")
        return
    
    invoice_number = selected[0]
    values = table.item(invoice_number,"values")
    current_remark = values[5]
    
    new_remark = simpledialog.askstring(
        "修改备注",
        "请输入备注：",
        initialvalue=current_remark,
        parent=root
    )
    
    if new_remark is None:
        return
    saved = update_remark(invoice_number,new_remark.strip())
    if saved:
        load_invoices()
    else:
        messagebox.showwarning("提示", "未找到这张发票，请刷新列表")

def load_invoices():
    # 清除旧行，避免刷新时重复显示
    for item in table.get_children():
        table.delete(item)

    invoices = list_invoices()

    status_names = {
        "available": "待使用",
        "reserved": "已预留",
        "used": "已使用",
        "unusable": "不可用",
    }

    for invoice in invoices:
        amount = Decimal(invoice["total_amount_fen"]) / 100

        table.insert(
            "",
            "end",
            iid=invoice["invoice_number"],
            values=(
                invoice["invoice_date"],
                invoice["seller"],
                f"{amount:.2f}",
                "已打印" if invoice["is_printed"] else "未打印",
                status_names.get(invoice["status"], invoice["status"]),
                invoice["remark"],
            ),
        )

    summary.set(f"共 {len(invoices)} 张发票")


init_db()

# 创建主窗口
root = tk.Tk()
root.title("个人发票管理")
root.geometry("1100x600")

toolbar = ttk.Frame(root, padding=10)
toolbar.pack(fill="x")

ttk.Button(
    toolbar,
    text="刷新列表",
    command=load_invoices,
).pack(side="left")

ttk.Button(
    toolbar,
    text="修改备注",
    command=edit_selected_remark,
).pack(side="left",padx=(8,0))

summary = tk.StringVar(value="共 0 张发票")
ttk.Label(toolbar, textvariable=summary).pack(side="right")

columns = ("date", "seller", "amount", "printed", "status", "remark")

table = ttk.Treeview(
    root,
    columns=columns,
    show="headings",
    selectmode="extended",
)

column_settings = [
    ("date", "开票日期", 110),
    ("seller", "商家名称", 320),
    ("amount", "金额（元）", 100),
    ("printed", "打印状态", 90),
    ("status", "使用状态", 90),
    ("remark", "备注", 250),
]

for key, title, width in column_settings:
    table.heading(key, text=title)
    table.column(key, width=width, anchor="w")

table.pack(fill="both", expand=True, padx=10, pady=(0, 10))

load_invoices()
root.mainloop()