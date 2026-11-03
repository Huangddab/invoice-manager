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
from tkinter import filedialog, messagebox, simpledialog, ttk

from invoice_manager import database
from invoice_manager.invoice_files import display_invoice_number
from invoice_manager.read_invoice import import_invoices
from invoice_manager.settings import load_settings, save_settings
from invoice_manager.folder_sync import FolderSync, fingerprint, scan_directories

CATEGORY = {"meal": "餐补", "other": "其他"}

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


def filter_invoices(invoices, query="", status="全部", printed="全部", category="全部"):
    query = query.strip().casefold()
    return [row for row in invoices
            if (not query or query in " ".join(str(row.get(key, "")) for key in
                ("invoice_number", "invoice_serial", "invoice_code", "seller", "buyer", "remark")).casefold())
            and (category == "全部" or CATEGORY.get(row.get("category", "meal")) == category)
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
        self.last_import_result = None
        self.import_result_window = None
        self.results = queue.Queue()
        settings = load_settings()
        self.scan_folders = settings.get('scan_folders', {})
        self.folder_sync = FolderSync()
        self.auto_sync = tk.BooleanVar(root, settings.get('auto_sync', True))
        self.sync_status = tk.StringVar(root)
        self.sync_scanning = False
        self.sync_results = queue.Queue()
        self.sync_pending = []
        self.query = tk.StringVar(root)
        self.status = tk.StringVar(root, "全部")
        self.printed = tk.StringVar(root, "全部")
        self.category = tk.StringVar(root, "全部")
        self.summary = tk.StringVar(root)
        self.selection_summary = tk.StringVar(root)
        self.month = tk.StringVar(root, date.today().strftime("%Y-%m"))
        self.days = tk.StringVar(root, "")
        self.allowance = tk.StringVar(root, str(load_settings().get("daily_allowance", "25")))
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
        from invoice_manager.workflow_panel import MonthlyWorkflow, HistoryPanel
        self.workflow = MonthlyWorkflow(self.month_tab, self.month, self.days, self.allowance, self.workflow_changed)
        self.history = HistoryPanel(self.history_tab, self.open_batch)
        outer = ttk.Frame(self.stock_tab, padding=16)
        outer.pack(fill="both", expand=True)
        header = ttk.Frame(outer)
        header.pack(fill="x")
        ttk.Label(header, text="我的发票", style="Title.TLabel").pack(side="left")
        self.import_controls = []
        self.other_import_button = ttk.Button(header, text="＋ 导入其他", command=lambda: self.choose_import('other'))
        self.other_import_button.pack(side="right", padx=4)
        self.import_button = ttk.Button(header, text="＋ 导入餐补", command=lambda: self.choose_import('meal'))
        self.import_button.pack(side="right", padx=4)
        scan_button = ttk.Menubutton(header, text="扫描文件夹 ▾")
        scan_menu = tk.Menu(scan_button, tearoff=False)
        scan_menu.add_command(label="扫描为餐补发票", command=lambda: self.scan_folder('meal'))
        scan_menu.add_command(label="扫描为其他发票", command=lambda: self.scan_folder('other'))
        scan_button.configure(menu=scan_menu)
        scan_button.pack(side="right", padx=4)
        self.import_controls.extend([self.import_button, self.other_import_button, scan_button])
        ttk.Button(header, text="刷新", command=self.refresh).pack(side="right", padx=8)
        ttk.Label(outer, textvariable=self.summary, style="Summary.TLabel").pack(anchor="w", pady=(0, 16))
        sync_bar = ttk.Frame(outer)
        sync_bar.pack(fill='x', pady=(0, 8))
        ttk.Checkbutton(sync_bar, text='自动同步扫描目录', variable=self.auto_sync, command=self.toggle_sync).pack(side='left')
        ttk.Button(sync_bar, text='同步目录设置', command=self.show_sync_folders).pack(side='left', padx=8)
        ttk.Label(sync_bar, textvariable=self.sync_status, wraplength=650).pack(side='left')
        filters = ttk.Frame(outer)
        filters.pack(fill="x", pady=(0, 12))
        ttk.Label(filters, text="搜索").pack(side="left")
        search = ttk.Entry(filters, textvariable=self.query, width=32)
        search.pack(side="left", padx=(8, 16))
        for label, variable, values in (("分类", self.category, ["全部", *CATEGORY.values()]), ("使用状态", self.status, ["全部", *STATUS.values()]),
                                        ("打印状态", self.printed, ["全部", "未打印", "已打印"])):
            ttk.Label(filters, text=label).pack(side="left")
            ttk.Combobox(filters, textvariable=variable, values=values, state="readonly", width=9).pack(side="left", padx=8)
        ttk.Button(filters, text="重置筛选", command=self.reset_filters).pack(side="right")
        frame = ttk.Frame(outer)
        frame.pack(fill="both", expand=True)
        columns = ("number", "category", "date", "seller", "amount", "printed", "status", "remark")
        self.table = ttk.Treeview(frame, columns=columns, show="headings", selectmode="extended")
        for key, title, width in zip(columns,
                ("发票号码", "分类", "开票日期", "商家名称", "金额（元）", "打印状态", "使用状态", "备注"),
                (180, 65, 100, 210, 100, 85, 85, 190)):
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
        category_actions = ttk.Frame(outer)
        category_actions.pack(fill="x", pady=4)
        ttk.Label(category_actions, text="所选分类：").pack(side="left")
        for code, label in CATEGORY.items():
            ttk.Button(category_actions, text=f"移至{label}", command=lambda value=code: self.move_category(value)).pack(side="left", padx=4)
        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=8)
        self.delete_button = ttk.Button(actions, text="删除记录", command=self.delete_selected)
        self.delete_button.pack(side="left", padx=6)
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
        self.import_details_button = ttk.Button(outer, text='查看本次处理详情', command=self.show_import_details, state='disabled')
        self.import_details_button.pack(anchor='w', pady=4)
        self.table.bind("<<TreeviewSelect>>", self.selection_changed)
        self.table.bind("<Double-1>", self.edit_remark)
        root.bind("<Control-f>", lambda event: search.focus_set())
        root.bind("<F5>", lambda event: self.refresh())
        for variable in (self.query, self.status, self.printed, self.category):
            variable.trace_add("write", lambda *args: self.render())
        self.refresh()
        self.update_sync_status()
        self.root.after(1000, self.check_sync)

    def refresh(self):
        try:
            self.rows = database.list_invoices()
            self.render()
        except sqlite3.Error as error:
            self.feedback.set(f"无法读取发票：{error}")

    def render(self):
        selected = set(self.table.selection())
        self.visible = filter_invoices(self.rows, self.query.get(), self.status.get(), self.printed.get(), self.category.get())
        self.table.delete(*self.table.get_children())
        for row in self.visible:
            self.table.insert("", "end", iid=row["invoice_number"], values=(
                display_invoice_number(row), CATEGORY[row["category"]], row["invoice_date"], row["seller"], money(row["total_amount_fen"]),
                "已打印" if row["is_printed"] else "未打印", STATUS.get(row["status"], row["status"]), row["remark"]))
        self.table.selection_set([row["invoice_number"] for row in self.visible if row["invoice_number"] in selected])
        self.summary.set(f"全部 {len(self.rows)} 张  ·  当前显示 {len(self.visible)} 张  ·  当前金额 ¥ {money(sum(r['total_amount_fen'] for r in self.visible))}")
        self.empty.configure(text="还没有发票，点击右上角「导入餐补」或「导入其他」开始。" if not self.rows else
                             "没有符合条件的发票，试试清空搜索或重置筛选。" if not self.visible else "")
        self.selection_changed()

    def reset_filters(self):
        self.query.set("")
        self.status.set("全部")
        self.printed.set("全部")
        self.category.set("全部")

    def select_all(self):
        self.table.selection_set(self.table.get_children())
        self.selection_changed()

    def selection_changed(self, event=None):
        selected = set(self.table.selection())
        total = sum(row["total_amount_fen"] for row in self.visible if row["invoice_number"] in selected)
        self.selection_summary.set(f"已选 {len(selected)} 张 · 合计 ¥ {money(total)}")
        self.delete_button.configure(state="normal" if selected else "disabled")
        self.open_button.configure(state="normal" if len(selected) == 1 else "disabled")
        for button in [*self.batch_buttons, self.status_menu]:
            button.configure(state="normal" if selected else "disabled")

    def edit_remark(self, event=None):
        if event is not None:
            number = self.table.identify_row(event.y)
            if not number:
                return
            self.table.selection_set(number)
        selected = self.table.selection()
        if len(selected) != 1:
            return
        row = next(row for row in self.rows if row["invoice_number"] == selected[0])
        remark = simpledialog.askstring("修改备注", f"发票 {display_invoice_number(row)}", initialvalue=row["remark"], parent=self.root)
        if remark is not None:
            self.apply_change(lambda: database.update_remark(selected[0], remark.strip()), "备注已保存")
        return "break"

    def delete_selected(self):
        selected = self.table.selection()
        if not selected:
            self.feedback.set('请先选择要删除的发票。')
            return
        if not messagebox.askyesno('删除发票记录', f'确认删除所选 {len(selected)} 张发票的数据库记录？\n原始 PDF 保留。已预留、已使用或关联报销单的发票不能删除。', parent=self.root, default=messagebox.NO):
            return
        try:
            count = database.delete_invoices(selected)
        except (ValueError, sqlite3.Error) as error:
            self.feedback.set(f'删除失败：{error}')
            messagebox.showerror('删除失败，本次未删除任何记录', str(error), parent=self.root)
            return
        self.workflow.inputs_changed()
        self.workflow_changed()
        self.feedback.set(f'已删除 {count} 条记录，原始 PDF 保留。')

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
        from invoice_manager.invoice_files import original_path
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

    def move_category(self, category):
        selected = self.table.selection()
        if not selected:
            self.feedback.set('请先选择发票。')
            return
        self.apply_change(lambda: database.update_category_batch(selected, category), f'已移至{CATEGORY[category]}。')
        self.workflow.refresh_other()

    def workflow_changed(self):
        self.refresh()
        self.history.reload()
        self.workflow.refresh_other()

    def open_batch(self, batch_id):
        batch = next((row for row in database.list_reimbursement_batches() if row['id'] == batch_id), None)
        if batch is None:
            messagebox.showerror('无法打开', '报销单已不存在，请刷新历史。', parent=self.root)
            return
        if batch['status'] == 'used':
            from invoice_manager.batch_view import BatchView
            window = tk.Toplevel(self.root)
            window.title('已完成报销单 · 查看 / 打印 / 导出')
            window.geometry('1180x700')
            BatchView(window, batch_id, self.workflow_changed)
            return
        if self.workflow.open_batch(batch_id):
            self.notebook.select(self.month_tab)

    def recommend(self):
        self.notebook.select(self.month_tab)
        self.workflow.generate()

    def create_batch(self):
        self.notebook.select(self.month_tab)
        self.workflow.generate()

    def show_batches(self):
        self.history.reload()
        self.notebook.select(self.history_tab)

    def scan_folder(self, category):
        if self.importing:
            return
        chosen = filedialog.askdirectory(parent=self.root, title=f"导入{CATEGORY[category]}：选择 PDF 文件夹")
        if not chosen:
            return
        folder = Path(chosen)
        try:
            paths = sorted(path for path in folder.iterdir() if path.is_file() and path.suffix.lower() == ".pdf")
        except OSError as error:
            self.feedback.set(f'扫描文件夹失败：{folder}：{error}')
            messagebox.showerror('扫描文件夹失败', f'{folder}\n{error}', parent=self.root)
            return
        folder = str(folder.resolve())
        previous = dict(self.scan_folders)
        # One source directory has one category; an explicit new scan changes its assignment.
        self.scan_folders = {key: path for key, path in self.scan_folders.items() if Path(path) != Path(folder)}
        self.scan_folders[category] = folder
        try:
            save_settings(scan_folders=self.scan_folders)
        except OSError as error:
            self.scan_folders = previous
            messagebox.showerror('保存同步目录失败', str(error), parent=self.root)
            return
        self.update_sync_status()
        if not paths:
            self.feedback.set('目录暂无 PDF，已记住该目录；开启自动同步后会检查新增发票。')
            return
        self.start_import(paths, category)

    def choose_import(self, category):
        if self.importing:
            return
        paths = filedialog.askopenfilenames(parent=self.root, title=f"导入{CATEGORY[category]}发票：选择 PDF（可多选）", filetypes=[("PDF 发票", "*.pdf"), ("所有文件", "*.*")])
        if paths:
            self.start_import(paths, category)

    def start_import(self, paths, category="meal", automatic=False, signatures=None):
        if self.importing:
            return
        if category not in CATEGORY:
            self.feedback.set('请选择餐补或其他分类。')
            return
        self.importing_category = category
        self.importing_automatic = automatic
        self.import_signatures = dict(signatures or {})
        if signatures is None:
            watched = {Path(folder) for folder in self.scan_folders.values()}
            for path in paths:
                path = Path(path).resolve()
                if path.parent in watched:
                    try:
                        self.import_signatures[str(path)] = fingerprint(path, category)
                    except OSError:
                        pass
        self.importing = True
        for control in self.import_controls:
            control.configure(state="disabled")
        self.feedback.set(f"正在导入{CATEGORY[category]}：{len(paths)} 个文件…")
        def worker():
            try:
                selected_paths = paths
                accepted = {}
                if automatic:
                    # A queued batch may have waited behind another import. Recheck stability.
                    for path, token in signatures.items():
                        try:
                            if fingerprint(path, category) == token:
                                accepted[path] = token
                        except OSError:
                            pass
                    selected_paths = list(accepted)
                result = import_invoices(selected_paths, category)
                if automatic:
                    result['_sync_signatures'] = accepted
                self.results.put(result)
            except Exception as error:
                self.results.put({"added": 0, "duplicates": 0, "errors": [(str(Path(path).resolve()), f"{type(error).__name__}：{error}") for path in paths]})
        threading.Thread(target=worker, daemon=True).start()
        self.root.after(100, self.poll_import)

    def poll_import(self):
        try:
            result = self.results.get_nowait()
        except queue.Empty:
            self.root.after(100, self.poll_import)
            return
        self.importing = False
        for control in self.import_controls:
            control.configure(state="normal")
        if not self.importing_automatic:
            self.reset_filters()
            self.category.set(CATEGORY[self.importing_category])
        self.refresh()
        self.workflow.refresh_other()
        summary = f"{CATEGORY[self.importing_category]}导入完成：新增 {result['added']} 张，重复跳过 {result['duplicates']} 张，失败 {len(result['errors'])} 张。"
        self.feedback.set(summary)
        completed_signatures = result.pop('_sync_signatures', self.import_signatures)
        if completed_signatures:
            try:
                self.folder_sync.remember(completed_signatures)
            except OSError as error:
                self.sync_status.set(f'同步记录未保存：{error}；重启后可能重新检查，但不会重复入库。')
        if self.importing_automatic:
            prefix = '自动同步' if self.auto_sync.get() else '自动同步已暂停，本次处理完成'
            self.sync_status.set(f'{prefix}：新增 {result["added"]}，重复 {result["duplicates"]}，失败 {len(result["errors"])}')
        self.last_import_result = dict(result, category=self.importing_category, summary=summary)
        self.import_details_button.configure(state='normal')
        if result['errors']:
            self.show_import_details()
        if self.sync_pending:
            self.root.after(0, self.import_next_sync)

    def update_sync_status(self):
        if not self.auto_sync.get():
            self.sync_status.set('已暂停')
        elif not self.scan_folders:
            self.sync_status.set('选择扫描目录后自动启用，每 10 秒检查一次')
        else:
            self.sync_status.set('已开启：' + '、'.join(CATEGORY[key] for key in self.scan_folders))

    def toggle_sync(self):
        try:
            save_settings(auto_sync=self.auto_sync.get())
        except OSError as error:
            messagebox.showerror('保存同步开关失败', str(error), parent=self.root)
        if not self.auto_sync.get():
            self.sync_pending.clear()
        self.update_sync_status()

    def show_sync_folders(self):
        window = tk.Toplevel(self.root)
        window.title('同步目录设置')
        window.geometry('800x250')
        window.transient(self.root)
        ttk.Label(window, text='每类记住一个目录，仅检查该目录内的 PDF。程序关闭期间不运行，重启后补查新增文件。', padding=12, wraplength=750).pack(anchor='w')
        for category in CATEGORY:
            row = ttk.Frame(window, padding=8)
            row.pack(fill='x')
            ttk.Label(row, text=f'{CATEGORY[category]}：{self.scan_folders.get(category, "未设置")}', wraplength=570).pack(side='left')
            def choose(value=category):
                window.destroy()
                self.scan_folder(value)
            def remove(value=category):
                folders = {key: path for key, path in self.scan_folders.items() if key != value}
                try:
                    save_settings(scan_folders=folders)
                except OSError as error:
                    messagebox.showerror('保存同步目录失败', str(error), parent=window)
                    return
                self.scan_folders = folders
                self.sync_pending.clear()
                self.update_sync_status()
                window.destroy()
            ttk.Button(row, text='选择目录', command=choose).pack(side='right')
            ttk.Button(row, text='移除', command=remove).pack(side='right', padx=6)
        ttk.Button(window, text='关闭', command=window.destroy).pack(pady=8)

    def check_sync(self):
        self.root.after(10000, self.check_sync)
        if not self.auto_sync.get() or not self.scan_folders or self.importing or self.sync_scanning:
            return
        if self.sync_pending:
            self.import_next_sync()
            return
        self.sync_scanning = True
        folders = dict(self.scan_folders)
        def worker():
            try:
                self.sync_results.put((folders, scan_directories(folders)))
            except Exception as error:
                self.sync_results.put((folders, ({}, {'扫描': f'{type(error).__name__}：{error}'})))
        threading.Thread(target=worker, daemon=True).start()
        self.root.after(100, self.poll_sync)

    def poll_sync(self):
        try:
            folders, (files, errors) = self.sync_results.get_nowait()
        except queue.Empty:
            self.root.after(100, self.poll_sync)
            return
        self.sync_scanning = False
        if not self.auto_sync.get() or folders != self.scan_folders:
            return
        ready = self.folder_sync.ready(files)
        if errors:
            self.sync_status.set('自动同步暂不可用：' + '；'.join(f'{path}：{error}' for path, error in errors.items()))
        elif not self.importing:
            self.update_sync_status()
        for category in CATEGORY:
            signatures = {path: token for path, token in ready.items() if token[0] == category}
            if signatures:
                self.sync_pending.append((category, signatures))
        self.import_next_sync()

    def import_next_sync(self):
        if self.importing or not self.auto_sync.get():
            return
        while self.sync_pending:
            category, signatures = self.sync_pending.pop(0)
            signatures = {path: token for path, token in signatures.items()
                          if self.folder_sync.attempted.get(path) != token
                          and self.scan_folders.get(category) == str(Path(path).parent)}
            if signatures:
                self.start_import(list(signatures), category, automatic=True, signatures=signatures)
                return

    def show_import_details(self):
        if self.last_import_result is None:
            return
        if self.import_result_window is not None and self.import_result_window.winfo_exists():
            self.import_result_window.destroy()
        from invoice_manager.import_results import ImportResultWindow
        self.import_result_window = ImportResultWindow(self.root, self.last_import_result, self.start_import)


def main():
    database.init_db()
    root = tk.Tk()
    InvoiceApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
