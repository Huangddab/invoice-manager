"""Desktop invoice workspace. Run with ``python app.py``."""
import os
import subprocess
import sys
import queue
import sqlite3
import threading
import tkinter as tk
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from tkinter import filedialog, ttk

import database
from read_invoice import import_invoices
from invoice_selector import select_invoice_combination

STATUS = {"available": "待使用", "reserved": "已预留", "used": "已使用", "unusable": "不可用"}


def open_document(path):
    if sys.platform == "win32":
        try:
            os.startfile(str(Path(path).resolve()))
        except OSError as error:
            if getattr(error, 'winerror', None) == 1155:
                raise OSError('请在 Windows 中将 Edge 或其他 PDF 阅读器设为 PDF 默认打开方式。') from error
            raise
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(Path(path).resolve())])


def money(fen):
    return f"{Decimal(fen) / 100:,.2f}"


def filter_invoices(invoices, query="", status="全部", printed="全部"):
    query = query.strip().casefold()
    return [row for row in invoices
            if (not query or query in " ".join(str(row[key]) for key in
                ("invoice_number", "seller", "buyer", "remark")).casefold())
            and (status == "全部" or STATUS.get(row["status"]) == status)
            and (printed == "全部" or bool(row["is_printed"]) == (printed == "已打印"))]


def reimbursement_inputs(month, days_text, allowance_text):
    try:
        parsed = datetime.strptime(month, "%Y-%m")
        if parsed.strftime("%Y-%m") != month:
            raise ValueError()
    except ValueError:
        raise ValueError("报销月份请填写 YYYY-MM，例如 2026-10")
    try:
        days = Decimal(days_text)
        allowance = Decimal(allowance_text)
        if not days.is_finite() or days != days.to_integral_value() or not 0 <= days <= 31:
            raise ValueError()
        if not allowance.is_finite() or allowance < 0 or allowance * 100 != (allowance * 100).to_integral_value():
            raise ValueError()
        if allowance * 100 > 9223372036854775807:
            raise ValueError()
    except (InvalidOperation, ValueError):
        raise ValueError("天数需为 0～31 的整数；每日标准需非负且最多两位小数")
    return month, int(days), int(allowance * 100)


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
        self.month = tk.StringVar(root, date.today().strftime("%Y-%m"))
        self.days = tk.StringVar(root, "20")
        self.allowance = tk.StringVar(root, "25")
        self.target_summary = tk.StringVar(root)
        self.feedback = tk.StringVar(root, "")
        root.title("个人发票管理")
        root.geometry("1180x720")
        root.minsize(900, 560)
        style = ttk.Style(root)
        style.configure("Treeview", rowheight=32)
        style.configure("Title.TLabel", font=("", 20, "bold"))
        style.configure("Summary.TLabel", font=("", 12))
        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill="both", expand=True)
        self.month_tab = ttk.Frame(self.notebook)
        self.stock_tab = ttk.Frame(self.notebook)
        self.history_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.month_tab, text="本月报销")
        self.notebook.add(self.stock_tab, text="发票库存")
        self.notebook.add(self.history_tab, text="报销历史")
        from workflow_panel import MonthlyWorkflow, HistoryPanel
        self.workflow = MonthlyWorkflow(self.month_tab, self.month, self.days, self.allowance, self.workflow_changed)
        self.history = HistoryPanel(self.history_tab, self.open_batch)
        outer = ttk.Frame(self.stock_tab, padding=16)
        outer.pack(fill="both", expand=True)
        header = ttk.Frame(outer)
        header.pack(fill="x")
        ttk.Label(header, text="我的发票", style="Title.TLabel").pack(side="left")
        self.import_button = ttk.Button(header, text="＋ 导入 PDF", command=self.choose_import)
        self.import_button.pack(side="right")
        ttk.Button(header, text="扫描发票目录", command=self.scan_folder).pack(side="right", padx=8)
        ttk.Button(header, text="刷新", command=self.refresh).pack(side="right", padx=8)
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
        self.remark = tk.StringVar(root)
        self.remark_entry = ttk.Entry(actions, textvariable=self.remark, width=25)
        self.remark_entry.pack(side="left", padx=6)
        self.save_remark_button = ttk.Button(actions, text="保存备注", command=self.save_remark)
        self.save_remark_button.pack(side="left")
        self.open_button = ttk.Button(actions, text="打开原 PDF", command=self.open_pdf)
        self.open_button.pack(side="left", padx=6)
        self.batch_buttons = []
        for text, printed in (("重置为未打印", False),):
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
        self.import_errors = tk.Text(outer, height=3, wrap="word")
        self.table.bind("<<TreeviewSelect>>", self.selection_changed)
        self.table.bind("<Double-1>", self.edit_remark)
        root.bind("<Control-f>", lambda event: search.focus_set())
        root.bind("<F5>", lambda event: self.refresh())
        for variable in (self.query, self.status, self.printed):
            variable.trace_add("write", lambda *args: self.render())
        for variable in (self.month, self.days, self.allowance):
            variable.trace_add("write", lambda *args: self.selection_changed())
        self.refresh()

    def refresh(self):
        try:
            self.rows = database.list_invoices()
            self.render()
        except sqlite3.Error as error:
            self.feedback.set(f"无法读取发票：{error}")

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
        self.open_button.configure(state="normal" if len(selected) == 1 else "disabled")
        self.save_remark_button.configure(state="normal" if len(selected) == 1 else "disabled")
        if len(selected) == 1:
            row = next(row for row in self.rows if row["invoice_number"] in selected)
            self.remark.set(row["remark"])
        try:
            _, days, allowance = reimbursement_inputs(self.month.get().strip(), self.days.get().strip(), self.allowance.get().strip())
            difference = total - days * allowance
            gap = f"超出 ¥ {money(difference)}" if difference > 0 else f"还差 ¥ {money(-difference)}" if difference < 0 else "刚好达到目标"
            self.target_summary.set(f"报销目标 ¥ {money(days * allowance)} · {gap}（按当前选择计算）")
        except ValueError as error:
            self.target_summary.set(str(error))
        for button in [*self.batch_buttons, self.status_menu]:
            button.configure(state="normal" if selected else "disabled")

    def edit_remark(self, event=None):
        selected = self.table.selection()
        if len(selected) != 1:
            return
        row = next(row for row in self.rows if row["invoice_number"] == selected[0])
        self.remark.set(row["remark"])
        self.remark_entry.focus_set()

    def save_remark(self):
        selected = self.table.selection()
        if len(selected) == 1:
            self.apply_change(lambda: database.update_remark(selected[0], self.remark.get().strip()), "备注已保存")

    def apply_change(self, operation, success):
        try:
            if not operation():
                raise ValueError("发票可能已被其他操作更改，请刷新后重试")
        except (sqlite3.Error, ValueError) as error:
            self.feedback.set(f"保存失败：{error}")
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
        if value == "used":
            self.feedback.set('请到「本月报销」或「报销历史」确认整单已报销，库存中不直接标记已使用。')
            return
        self.apply_change(lambda: database.update_status_batch(numbers, value), f"已将 {len(numbers)} 张发票设为{STATUS[value]}")

    def open_pdf(self):
        selected = self.table.selection()
        if len(selected) != 1:
            return
        from invoice_files import original_path
        try:
            row = next(row for row in self.rows if row["invoice_number"] == selected[0])
            path = original_path(row)
        except OSError as error:
            self.feedback.set(f"找不到原 PDF：{error}")
            return
        if not path or not Path(path).is_file():
            self.feedback.set("原文件可能已移动或删除，请核对文件位置。")
            return
        try:
            open_document(path)
            self.feedback.set(f"正在打开 PDF：{selected[0]}")
        except OSError as error:
            self.feedback.set(f"无法打开 PDF：{error}")

    def workflow_changed(self):
        self.refresh()
        self.history.reload()

    def open_batch(self, batch_id):
        if self.workflow.open_batch(batch_id):
            self.notebook.select(self.month_tab)

    def recommend(self):
        self.notebook.select(self.month_tab)
        self.workflow.generate()

    def create_batch(self):
        self.notebook.select(self.month_tab)
        self.workflow.confirm_proposal()

    def show_batches(self):
        self.history.reload()
        self.notebook.select(self.history_tab)

    def scan_folder(self):
        chosen = filedialog.askdirectory(parent=self.root, title="选择需要扫描新增 PDF 的文件夹")
        if not chosen:
            return
        folder = Path(chosen)
        paths = sorted(path for path in folder.glob("*") if path.is_file() and path.suffix.lower() == ".pdf")
        if not paths:
            self.feedback.set("发票目录中没有 PDF，请点击「导入 PDF」选择文件。")
            return
        self.start_import(paths)

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
        self.import_errors.pack_forget()
        if result["errors"]:
            self.import_errors.configure(state="normal")
            self.import_errors.delete("1.0", "end")
            self.import_errors.insert("1.0", "\n".join(f"{name}：{error}" for name, error in result["errors"]))
            self.import_errors.configure(state="disabled")
            self.import_errors.pack(fill="x", pady=6)


def main():
    database.init_db()
    root = tk.Tk()
    InvoiceApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
