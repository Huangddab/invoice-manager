"""Desktop invoice workspace. Run with ``python app.py``."""
import queue
import sqlite3
import threading
import tkinter as tk
from decimal import Decimal
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import database
from read_invoice import import_invoices

STATUS = {"available": "待使用", "reserved": "已预留", "used": "已使用", "unusable": "不可用"}


def money(fen):
    return f"{Decimal(fen) / 100:,.2f}"


def filter_invoices(invoices, query="", status="全部", printed="全部"):
    query = query.strip().casefold()
    return [row for row in invoices
            if (not query or query in " ".join(str(row[key]) for key in
                ("invoice_number", "seller", "remark")).casefold())
            and (status == "全部" or STATUS.get(row["status"]) == status)
            and (printed == "全部" or bool(row["is_printed"]) == (printed == "已打印"))]


class InvoiceApp:
    def __init__(self, root):
        self.root = root
        self.rows = []
        self.importing = False
        self.results = queue.Queue()
        self.query = tk.StringVar(root)
        self.status = tk.StringVar(root, "全部")
        self.printed = tk.StringVar(root, "全部")
        self.summary = tk.StringVar(root)
        self.selection_summary = tk.StringVar(root)
        self.feedback = tk.StringVar(root, "先导入 PDF，再选择发票进行操作。")
        root.title("个人发票管理")
        root.geometry("1180x720")
        root.minsize(900, 560)
        style = ttk.Style(root)
        style.configure("Treeview", rowheight=32)
        style.configure("Title.TLabel", font=("", 20, "bold"))
        style.configure("Summary.TLabel", font=("", 12))
        outer = ttk.Frame(root, padding=20)
        outer.pack(fill="both", expand=True)
        header = ttk.Frame(outer)
        header.pack(fill="x")
        ttk.Label(header, text="我的发票", style="Title.TLabel").pack(side="left")
        self.import_button = ttk.Button(header, text="＋ 导入 PDF", command=self.choose_import)
        self.import_button.pack(side="right")
        ttk.Button(header, text="刷新", command=self.refresh).pack(side="right", padx=8)
        ttk.Label(outer, text="集中管理发票、打印进度和报销使用状态").pack(anchor="w", pady=(4, 14))
        ttk.Label(outer, textvariable=self.summary, style="Summary.TLabel").pack(anchor="w", pady=(0, 16))
        filters = ttk.Frame(outer)
        filters.pack(fill="x", pady=(0, 12))
        ttk.Label(filters, text="搜索").pack(side="left")
        search = ttk.Entry(filters, textvariable=self.query, width=32)
        search.pack(side="left", padx=(8, 16))
        for label, variable, values in (("使用状态", self.status, ["全部", *STATUS.values()]),
                                        ("打印状态", self.printed, ["全部", "未打印", "已打印"])):
            ttk.Label(filters, text=label).pack(side="left")
            ttk.Combobox(filters, textvariable=variable, values=values, state="readonly", width=9).pack(side="left", padx=8)
        ttk.Button(filters, text="重置筛选", command=self.reset_filters).pack(side="right")
        ttk.Label(outer, text="支持搜索发票号码、商家和备注；按 Ctrl / Shift 可多选，双击可修改备注。").pack(anchor="w", pady=(0, 8))
        frame = ttk.Frame(outer)
        frame.pack(fill="both", expand=True)
        columns = ("number", "date", "seller", "amount", "printed", "status", "remark")
        self.table = ttk.Treeview(frame, columns=columns, show="headings", selectmode="extended")
        for key, title, width in zip(columns,
                ("发票号码", "开票日期", "商家名称", "金额（元）", "打印状态", "使用状态", "备注"),
                (190, 100, 230, 100, 85, 85, 210)):
            self.table.heading(key, text=title)
            self.table.column(key, width=width, minwidth=75, anchor="e" if key == "amount" else "w")
        vertical = ttk.Scrollbar(frame, orient="vertical", command=self.table.yview)
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=self.table.xview)
        self.table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.table.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.empty = ttk.Label(outer, text="")
        self.empty.pack(anchor="w", pady=6)
        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=8)
        self.edit_button = ttk.Button(actions, text="修改备注", command=self.edit_remark)
        self.edit_button.pack(side="left")
        self.batch_buttons = []
        for text, printed in (("标记已打印", True), ("标记未打印", False)):
            button = ttk.Button(actions, text=text, command=lambda value=printed: self.mark_printed(value))
            button.pack(side="left", padx=6)
            self.batch_buttons.append(button)
        self.status_menu = ttk.Menubutton(actions, text="设置使用状态 ▾")
        menu = tk.Menu(self.status_menu, tearoff=False)
        for code, label in STATUS.items():
            menu.add_command(label=label, command=lambda value=code: self.mark_status(value))
        self.status_menu.configure(menu=menu)
        self.status_menu.pack(side="left", padx=6)
        ttk.Button(actions, text="全选当前列表", command=self.select_all).pack(side="right")
        ttk.Label(outer, textvariable=self.selection_summary).pack(anchor="w", pady=4)
        ttk.Separator(outer).pack(fill="x", pady=8)
        ttk.Label(outer, textvariable=self.feedback, wraplength=1050).pack(anchor="w")
        self.table.bind("<<TreeviewSelect>>", self.selection_changed)
        self.table.bind("<Double-1>", self.edit_remark)
        root.bind("<Control-f>", lambda event: search.focus_set())
        root.bind("<F5>", lambda event: self.refresh())
        for variable in (self.query, self.status, self.printed):
            variable.trace_add("write", lambda *args: self.render())
        self.refresh()

    def refresh(self):
        try:
            self.rows = database.list_invoices()
            self.render()
        except sqlite3.Error as error:
            messagebox.showerror("无法读取发票", str(error), parent=self.root)

    def render(self):
        selected = set(self.table.selection())
        self.visible = filter_invoices(self.rows, self.query.get(), self.status.get(), self.printed.get())
        self.table.delete(*self.table.get_children())
        for row in self.visible:
            self.table.insert("", "end", iid=row["invoice_number"], values=(
                row["invoice_number"], row["invoice_date"], row["seller"], money(row["total_amount_fen"]),
                "已打印" if row["is_printed"] else "未打印", STATUS.get(row["status"], row["status"]), row["remark"]))
        self.table.selection_set([row["invoice_number"] for row in self.visible if row["invoice_number"] in selected])
        self.summary.set(f"全部 {len(self.rows)} 张  ·  当前显示 {len(self.visible)} 张  ·  当前金额 ¥ {money(sum(r['total_amount_fen'] for r in self.visible))}")
        self.empty.configure(text="还没有发票，点击右上角「导入 PDF」开始。" if not self.rows else
                             "没有符合条件的发票，试试清空搜索或重置筛选。" if not self.visible else "")
        self.selection_changed()

    def reset_filters(self):
        self.query.set("")
        self.status.set("全部")
        self.printed.set("全部")

    def select_all(self):
        self.table.selection_set(self.table.get_children())
        self.selection_changed()

    def selection_changed(self, event=None):
        selected = set(self.table.selection())
        total = sum(row["total_amount_fen"] for row in self.visible if row["invoice_number"] in selected)
        self.selection_summary.set(f"已选 {len(selected)} 张 · 合计 ¥ {money(total)}")
        self.edit_button.configure(state="normal" if len(selected) == 1 else "disabled")
        for button in [*self.batch_buttons, self.status_menu]:
            button.configure(state="normal" if selected else "disabled")

    def edit_remark(self, event=None):
        selected = self.table.selection()
        if len(selected) != 1:
            return
        row = next(row for row in self.rows if row["invoice_number"] == selected[0])
        remark = simpledialog.askstring("修改备注", f"发票 {selected[0]}\n请输入备注（清空可删除备注）：", initialvalue=row["remark"], parent=self.root)
        if remark is not None:
            self.apply_change(lambda: database.update_remark(selected[0], remark.strip()), "备注已保存")

    def apply_change(self, operation, success):
        try:
            if not operation():
                raise ValueError("发票可能已被其他操作更改，请刷新后重试")
        except (sqlite3.Error, ValueError) as error:
            messagebox.showerror("保存失败", str(error), parent=self.root)
            return
        self.refresh()
        self.feedback.set(success)

    def mark_printed(self, value):
        numbers = self.table.selection()
        if numbers:
            self.apply_change(lambda: database.update_printed_batch(numbers, value),
                              f"已将 {len(numbers)} 张发票标记为{'已打印' if value else '未打印'}")

    def mark_status(self, value):
        numbers = self.table.selection()
        if not numbers:
            return
        if value in ("used", "unusable") and not messagebox.askyesno("确认使用状态", f"将所选 {len(numbers)} 张发票设为「{STATUS[value]}」？", parent=self.root):
            return
        self.apply_change(lambda: database.update_status_batch(numbers, value), f"已将 {len(numbers)} 张发票设为{STATUS[value]}")

    def choose_import(self):
        if self.importing:
            return
        paths = filedialog.askopenfilenames(parent=self.root, title="选择要导入的发票 PDF（可多选）", filetypes=[("PDF 发票", "*.pdf"), ("所有文件", "*.*")])
        if paths:
            self.start_import(paths)

    def start_import(self, paths):
        if self.importing:
            return
        self.importing = True
        self.import_button.configure(state="disabled")
        self.feedback.set(f"正在读取 {len(paths)} 个文件，请稍候…")
        def worker():
            try:
                self.results.put(import_invoices(paths))
            except Exception as error:
                self.results.put({"added": 0, "duplicates": 0, "errors": [("导入失败", str(error))]})
        threading.Thread(target=worker, daemon=True).start()
        self.root.after(100, self.poll_import)

    def poll_import(self):
        try:
            result = self.results.get_nowait()
        except queue.Empty:
            self.root.after(100, self.poll_import)
            return
        self.importing = False
        self.import_button.configure(state="normal")
        self.refresh()
        summary = f"导入完成：新增 {result['added']} 张，重复跳过 {result['duplicates']} 张，失败 {len(result['errors'])} 张。"
        self.feedback.set(summary)
        if result["errors"]:
            window = tk.Toplevel(self.root)
            window.title("导入结果 · 需要核对的文件")
            window.geometry("650x350")
            ttk.Label(window, text=summary, padding=12).pack(anchor="w")
            text = tk.Text(window, wrap="word", padx=12, pady=12)
            text.pack(fill="both", expand=True)
            text.insert("1.0", "\n\n".join(f"{name}\n{error}" for name, error in result["errors"]))
            text.configure(state="disabled")
            ttk.Button(window, text="关闭", command=window.destroy).pack(pady=10)


def main():
    database.init_db()
    root = tk.Tk()
    InvoiceApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
