import os
import sqlite3
import tkinter as tk
from decimal import Decimal
from pathlib import Path
from tkinter import ttk, simpledialog, messagebox
from read_invoice import scan_invoice_folder

from database import (
    init_db,
    list_invoices,
    update_remark,
    update_printed_batch,
    get_invoice_file_path,
)

# 更新选中发票的数量和金额
def update_selection_summary(event=None):
    selected = table.selection()
    total = Decimal("0.00")

    for invoice_number in selected:
        values = table.item(invoice_number, "values")
        total += Decimal(values[2])

    selection_summary.set(
        f"已选 {len(selected)} 张，合计 {total:.2f} 元"
    )


# 从数据库加载发票，并按打印状态筛选
def load_invoices():
    try:
        invoices = list_invoices()
    except sqlite3.Error as error:
        messagebox.showerror(
            "加载失败", str(error), parent=root
        )
        return
    # get 获取变量当前的值 set是修改
    selected_filter = print_filter.get()

    if selected_filter == "未打印":
        invoices = [
            invoice
            for invoice in invoices
            if invoice["is_printed"] == 0
        ]
    elif selected_filter == "已打印":
        invoices = [
            invoice
            for invoice in invoices
            if invoice["is_printed"] == 1
        ]

    # 查询成功后再清除旧行
    for item in table.get_children():
        table.delete(item)

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
                status_names.get(
                    invoice["status"], invoice["status"]
                ),
                invoice["remark"],
            ),
        )

    summary.set(f"当前显示 {len(invoices)} 张发票")
    update_selection_summary()

# 扫描发票文件夹并更新数据库
def scan_and_reload():
    try:
        result = scan_invoice_folder()
    except Exception as error:
        messagebox.showerror(
            "扫描失败", str(error), parent=root
        )
        return
    print_filter.set("全部")
    load_invoices()
    
    text = (
        f"新增 {result['added']} 张，"
        f"重复 {result['duplicate']} 张，"
        f"失败 {len(result['errors'])} 份文件"
    )
    if result["errors"]:
        text += "\n\n" + "\n".join(result["errors"])
        messagebox.showwarning("扫描结果", text, parent=root)
    else:
        messagebox.showinfo("扫描结果", text, parent=root)

# 打开选中发票的 PDF
def open_selected_invoice():
    selected = table.selection()

    if len(selected) != 1:
        messagebox.showinfo(
            "提示", "请选中一张发票后打开", parent=root
        )
        return

    try:
        file_path = get_invoice_file_path(selected[0])
    except sqlite3.Error as error:
        messagebox.showerror(
            "查询失败", str(error), parent=root
        )
        return

    if not file_path:
        messagebox.showwarning(
            "提示", "未找到发票对应的文件路径", parent=root
        )
        return

    path = Path(file_path)

    if not path.is_file():
        messagebox.showwarning(
            "文件不存在",
            f"原发票可能已被移动或删除：\n{path}",
            parent=root,
        )
        return

    try:
        os.startfile(str(path))
    except OSError as error:
        messagebox.showerror(
            "打开失败", str(error), parent=root
        )


# 批量修改打印状态
def mark_selected_printed(is_printed):
    selected = table.selection()

    if not selected:
        messagebox.showinfo(
            "提示", "请先选择要标记的发票", parent=root
        )
        return

    try:
        count = update_printed_batch(selected, is_printed)
    except (sqlite3.Error, ValueError) as error:
        messagebox.showerror(
            "更新失败", str(error), parent=root
        )
        return

    load_invoices()

    state = "已打印" if is_printed else "未打印"
    messagebox.showinfo(
        "完成",
        f"已将 {count} 张发票标记为{state}",
        parent=root,
    )


# 修改一张发票的备注
def edit_selected_remark():
    selected = table.selection()

    if len(selected) != 1:
        messagebox.showinfo(
            "提示", "请选中一张发票后修改备注", parent=root
        )
        return

    invoice_number = selected[0]
    values = table.item(invoice_number, "values")
    current_remark = values[5]

    new_remark = simpledialog.askstring(
        "修改备注",
        "请输入备注：",
        initialvalue=current_remark,
        parent=root,
    )

    # 点击取消时返回 None；空字符串表示清除备注
    if new_remark is None:
        return

    try:
        saved = update_remark(
            invoice_number, new_remark.strip()
        )
    except sqlite3.Error as error:
        messagebox.showerror(
            "保存失败", str(error), parent=root
        )
        return

    if saved:
        load_invoices()
    else:
        messagebox.showwarning(
            "提示", "未找到这张发票，请刷新列表", parent=root
        )


# 创建窗口
root = tk.Tk()
root.title("个人发票管理")
root.geometry("1100x600")
root.minsize(900, 450)

try:
    init_db()
except sqlite3.Error as error:
    messagebox.showerror(
        "数据库初始化失败", str(error), parent=root
    )
    root.destroy()
    raise SystemExit(1)


# 先创建文字变量，再创建使用它们的控件
print_filter = tk.StringVar(master=root, value="全部")
summary = tk.StringVar(
    master=root, value="当前显示 0 张发票"
)
selection_summary = tk.StringVar(
    master=root, value="已选 0 张，合计 0.00 元"
)


# 第一行：操作按钮
toolbar = ttk.Frame(root, padding=10)
toolbar.pack(fill="x")

ttk.Button(
    toolbar,
    text="刷新列表",
    command=load_invoices,
).pack(side="left")

ttk.Button(
    toolbar,
    text="扫描新增发票",
    command=scan_and_reload,
).pack(side="left", padx=(8, 0))

ttk.Button(
    toolbar,
    text="修改备注",
    command=edit_selected_remark,
).pack(side="left", padx=(8, 0))

ttk.Button(
    toolbar,
    text="标记已打印",
    command=lambda: mark_selected_printed(True),
).pack(side="left", padx=(8, 0))

ttk.Button(
    toolbar,
    text="标记未打印",
    command=lambda: mark_selected_printed(False),
).pack(side="left", padx=(8, 0))

ttk.Button(
    toolbar,
    text="打开原发票",
    command=open_selected_invoice,
).pack(side="left", padx=(8, 0))

ttk.Label(
    toolbar,
    textvariable=summary,
).pack(side="right")


# 第二行：打印状态筛选
filter_bar = ttk.Frame(root, padding=(10, 0, 10, 10))
filter_bar.pack(fill="x")

# Label绑定变量
ttk.Label(
    filter_bar,
    text="打印状态：",
).pack(side="left")

filter_box = ttk.Combobox(
    filter_bar,
    textvariable=print_filter,
    values=("全部", "未打印", "已打印"),
    state="readonly",
    width=10,
)
filter_box.pack(side="left")

filter_box.bind(
    "<<ComboboxSelected>>",
    lambda event: load_invoices(),
)


# 底部：选中数量与金额
ttk.Label(
    root,
    textvariable=selection_summary,
    padding=10,
    anchor="w",
).pack(side="bottom", fill="x")


# 中间：表格及滚动条
table_frame = ttk.Frame(root)
table_frame.pack(
    fill="both", expand=True, padx=10, pady=(0, 5)
)
table_frame.rowconfigure(0, weight=1)
table_frame.columnconfigure(0, weight=1)

columns = (
    "date", "seller", "amount", "printed", "status", "remark"
)

table = ttk.Treeview(
    table_frame,
    columns=columns,
    show="headings",
    selectmode="extended",
)

column_settings = [
    ("date", "开票日期", 110),
    ("seller", "商家名称", 300),
    ("amount", "金额（元）", 100),
    ("printed", "打印状态", 90),
    ("status", "使用状态", 90),
    ("remark", "备注", 250),
]

for key, title, width in column_settings:
    table.heading(key, text=title,anchor="center")
    table.column(
        key,
        width=width,
        minwidth=80,
        anchor="center",
    )

vertical_scroll = ttk.Scrollbar(
    table_frame,
    orient="vertical",
    command=table.yview,
)
horizontal_scroll = ttk.Scrollbar(
    table_frame,
    orient="horizontal",
    command=table.xview,
)

table.configure(
    yscrollcommand=vertical_scroll.set,
    xscrollcommand=horizontal_scroll.set,
)

table.grid(row=0, column=0, sticky="nsew")
vertical_scroll.grid(row=0, column=1, sticky="ns")
horizontal_scroll.grid(row=1, column=0, sticky="ew")

# 表格创建完成后，再绑定事件
table.bind(
    "<<TreeviewSelect>>", update_selection_summary
)

# 所有控件创建完成后，再加载数据
load_invoices()
root.mainloop()