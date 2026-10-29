"""Invoice checkboxes are for output operations, independent of ticket selection."""
from decimal import Decimal
from pathlib import Path
import queue
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, ttk

import database
from invoice_files import batch_export_name, export_zip, original_path, prepare_print_pdf, print_command, submit_print


def amount(fen):
    return f'{Decimal(fen) / 100:.2f}'


class BatchView:
    def __init__(self, parent, batch_id, changed=lambda: None):
        self.batch_id = batch_id
        self.changed = changed
        self.checked = set()
        self.busy = False
        self.window = ttk.Frame(parent)
        self.window.pack(fill='both', expand=True)
        self.pending_print = None
        self.pending_action = None
        self.header = tk.StringVar(self.window)
        self.summary = tk.StringVar(self.window)
        self.feedback = tk.StringVar(self.window, '勾选仅用于打印和导出；这些操作不会确认使用发票。')
        ttk.Label(self.window, textvariable=self.header, padding=12, font=('', 13, 'bold')).pack(anchor='w')
        controls = ttk.Frame(self.window, padding=(12, 0))
        controls.pack(fill='x')
        ttk.Button(controls, text='全选', command=lambda: self.check_all(True)).pack(side='left')
        ttk.Button(controls, text='取消全选', command=lambda: self.check_all(False)).pack(side='left', padx=8)
        ttk.Button(controls, text='打开原 PDF', command=self.open_pdf).pack(side='left')
        ttk.Button(controls, text='勾选 PDF 打包导出', command=self.export).pack(side='right', padx=8)
        self.print_button = ttk.Button(controls, text='批量打印 / 重新打印', command=self.print_checked)
        self.print_button.pack(side='right')
        frame = ttk.Frame(self.window, padding=12)
        columns = ('check', 'number', 'date', 'buyer', 'seller', 'amount', 'printed', 'remark')
        self.tree = ttk.Treeview(frame, columns=columns, show='headings', selectmode='browse')
        for key, title, width in zip(columns, ('勾选', '发票号码', '开票日期', '购买方', '销售方', '真实金额', '打印状态', '备注'),
                                    (55, 180, 100, 160, 200, 95, 85, 160)):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, minwidth=50)
        scroll = ttk.Scrollbar(frame, orient='vertical', command=self.tree.yview)
        horizontal = ttk.Scrollbar(frame, orient='horizontal', command=self.tree.xview)
        self.tree.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        self.tree.grid(row=0, column=0, sticky='nsew')
        scroll.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.tree.bind('<Button-1>', self.toggle)
        self.tree.bind('<space>', self.toggle_keyboard)
        ttk.Label(self.window, textvariable=self.summary, padding=(12, 4)).pack(anchor='w')
        ttk.Label(self.window, text='打印固定：A5 · 横向 · 黑白 · 每张票一页 · 等比例完整缩放（多页 PDF 需人工核对）', padding=(12, 4)).pack(anchor='w')
        ttk.Label(self.window, textvariable=self.feedback, padding=12, wraplength=1000).pack(anchor='w')
        self.confirmation = ttk.Frame(self.window, padding=(12, 4))
        self.confirmation.pack(fill='x')
        self.confirm_button = ttk.Button(self.confirmation, text='确认', command=self.confirm_pending)
        self.dismiss_button = ttk.Button(self.confirmation, text='取消 / 未全部成功', command=self.dismiss_pending)
        lifecycle = ttk.Frame(self.window, padding=12)
        lifecycle.pack(fill='x')
        self.finish_button = ttk.Button(lifecycle, text='确认已报销', command=lambda: self.request_finish('used'))
        self.finish_button.pack(side='right', padx=6)
        self.cancel_button = ttk.Button(lifecycle, text='取消此报销单', command=lambda: self.request_finish('cancelled'))
        self.cancel_button.pack(side='right', padx=6)
        self.reload()
        self.check_all(True)
        frame.pack(fill='both', expand=True)

    def reload(self):
        batches = database.list_reimbursement_batches()
        batch = next(row for row in batches if row['id'] == self.batch_id)
        self.batch = batch
        self.batch_status = batch['status']
        self.rows = database.reimbursement_invoices(self.batch_id)
        target = batch['work_days'] * batch['daily_allowance_fen']
        total = sum(row['total_amount_fen'] for row in self.rows)
        state = {'reserved': '待报销（已预留）', 'used': '已报销', 'cancelled': '已取消'}[batch['status']]
        self.header.set(f"{batch['reimbursement_month']} · 报销单 #{self.batch_id} · {state}\n目标 ¥ {amount(target)}  |  {len(self.rows)} 张，合计 ¥ {amount(total)}  |  差额 ¥ {amount(total-target)}")
        for button in (self.finish_button, self.cancel_button):
            button.configure(state='normal' if self.batch_status == 'reserved' else 'disabled')
        self.render()

    def render(self):
        self.tree.delete(*self.tree.get_children())
        for row in self.rows:
            self.tree.insert('', 'end', iid=row['invoice_number'], values=(
                '☑' if row['invoice_number'] in self.checked else '☐', row['invoice_number'], row['invoice_date'],
                row['buyer'], row['seller'], amount(row['total_amount_fen']), '已打印' if row['is_printed'] else '未打印', row['remark']))
        rows = self.selected_rows()
        self.summary.set(f'已勾选 {len(rows)} 张 · ¥ {amount(sum(row["total_amount_fen"] for row in rows))}（点击方框或按空格切换）')

    def selected_rows(self):
        return [row for row in self.rows if row['invoice_number'] in self.checked]

    def check_all(self, value):
        self.checked = {row['invoice_number'] for row in self.rows} if value else set()
        self.render()

    def toggle_number(self, number):
        if number in self.checked:
            self.checked.remove(number)
        else:
            self.checked.add(number)
        self.render()
        self.tree.focus(number)
        self.tree.selection_set(number)

    def toggle(self, event):
        number = self.tree.identify_row(event.y)
        if number and self.tree.identify_column(event.x) == '#1':
            self.toggle_number(number)
            return 'break'

    def toggle_keyboard(self, event):
        number = self.tree.focus()
        if number:
            self.toggle_number(number)
        return 'break'

    def require_rows(self):
        rows = self.selected_rows()
        if not rows:
            self.feedback.set('请先勾选要打印或导出的发票。')
        return rows

    def export(self):
        rows = self.require_rows()
        if not rows:
            return
        path = filedialog.asksaveasfilename(parent=self.window, title='导出原始 PDF 压缩包', defaultextension='.zip',
                    initialfile=batch_export_name(self.batch['reimbursement_month'], self.batch['work_days'] * self.batch['daily_allowance_fen'], rows), filetypes=[('ZIP 压缩包', '*.zip')])
        if not path:
            return
        try:
            count = export_zip(rows, path)
        except (OSError, ValueError) as error:
            self.feedback.set(f'导出失败：{error}')
            return
        self.feedback.set(f'已导出 {count} 张原始 PDF：{path}。打印及使用状态均未改变。')

    def open_pdf(self):
        selected = self.tree.selection()
        if not selected:
            return
        row = next(row for row in self.rows if row['invoice_number'] == selected[0])
        try:
            from app import open_document
            open_document(original_path(row))
        except OSError as error:
            self.feedback.set(f'无法打开原 PDF：{error}')

    def print_checked(self):
        if self.busy or self.pending_print is not None or self.pending_action is not None:
            self.feedback.set('请先处理下方的确认操作。')
            return
        rows = self.require_rows()
        if not rows:
            return
        temporary = tempfile.TemporaryDirectory(prefix='invoice-print-')
        pdf = Path(temporary.name) / 'A5-landscape-grayscale.pdf'
        try:
            command = print_command(pdf)
        except OSError as error:
            temporary.cleanup()
            self.feedback.set(f'无法启动打印：{error}。请检查系统打印机及依赖安装：python -m pip install -r requirements.txt')
            return
        self.busy = True
        self.print_button.configure(state='disabled')
        self.feedback.set('正在准备打印副本并等待打印程序；原始 PDF 不会改变…')
        results = queue.Queue()
        def worker():
            try:
                prepare_print_pdf(rows, pdf)
                submit_print(command)
                results.put(None)
            except Exception as error:
                results.put(error)
        threading.Thread(target=worker, daemon=True).start()
        def poll():
            if not self.window.winfo_exists():
                if results.empty():
                    self.window.master.after(100, poll)
                else:
                    temporary.cleanup()
                return
            try:
                error = results.get_nowait()
            except queue.Empty:
                self.window.after(100, poll)
                return
            temporary.cleanup()
            self.busy = False
            self.print_button.configure(state='normal')
            if error is not None:
                self.feedback.set(f'打印失败：{error}。未修改打印状态。')
                return
            self.pending_print = [row['invoice_number'] for row in rows]
            self.feedback.set(f'请核对实际出纸：本次 {len(rows)} 张是否全部打印成功？取消、未出纸或部分失败请选择「未全部成功」。')
            self.show_confirmation('全部实际打印成功')
        self.window.after(100, poll)


    def show_confirmation(self, label):
        self.confirm_button.configure(text=label)
        self.confirm_button.pack(side='left', padx=4)
        self.dismiss_button.pack(side='left', padx=4)

    def dismiss_pending(self):
        self.pending_print = None
        self.pending_action = None
        self.confirm_button.pack_forget()
        self.dismiss_button.pack_forget()
        self.feedback.set('已取消确认，打印和报销状态均未改变。')

    def request_finish(self, action):
        if self.busy or self.pending_print is not None:
            self.feedback.set('请先核对本次打印结果，再处理报销单。')
            return
        self.pending_action = action
        if action == 'used':
            self.feedback.set('确认这张报销单已实际用于报销？整单发票将标记已使用，不能再次使用。打印或导出不会代替此确认。')
            self.show_confirmation('确认整单已报销')
        else:
            self.feedback.set('取消此报销单后，整单发票恢复待使用。是否继续？')
            self.show_confirmation('确认取消并释放预留')

    def confirm_pending(self):
        try:
            if self.pending_print is not None:
                database.update_printed_batch(self.pending_print, True)
                feedback = '已根据实际出纸确认标记已打印；报销状态未改变。'
            elif self.pending_action is not None:
                database.finish_reimbursement_batch(self.batch_id, self.pending_action)
                feedback = '已确认报销，整单发票已使用。' if self.pending_action == 'used' else '报销单已取消，发票已释放。'
            else:
                return
        except (ValueError, database.sqlite3.Error) as error:
            self.feedback.set(f'保存失败：{error}')
            return
        self.pending_print = None
        self.pending_action = None
        self.confirm_button.pack_forget()
        self.dismiss_button.pack_forget()
        self.reload()
        self.changed()
        self.feedback.set(feedback)
