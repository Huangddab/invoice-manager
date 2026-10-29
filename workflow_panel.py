"""Embedded monthly reimbursement flow and history; no application dialogs."""
import sqlite3
from tkinter import ttk
import tkinter as tk

import database
from batch_view import BatchView
from invoice_selector import select_invoice_combination


class MonthlyWorkflow:
    def __init__(self, parent, month, days, allowance, changed):
        self.month, self.days, self.allowance = month, days, allowance
        self.changed = changed
        self.proposal = None
        self.batch_view = None
        self.feedback = tk.StringVar(parent, '')
        self.total = tk.StringVar(parent)
        self.stage = tk.StringVar(parent, '本月报销')
        ttk.Label(parent, textvariable=self.stage, padding=12, font=('', 12, 'bold')).pack(anchor='w')
        form = ttk.Frame(parent, padding=12)
        form.pack(fill='x')
        for label, variable, width in [('报销月份', month, 9), ('实际天数', days, 5), ('每日金额（元）', allowance, 8)]:
            ttk.Label(form, text=label).pack(side='left', padx=(0, 5))
            ttk.Entry(form, textvariable=variable, width=width).pack(side='left', padx=(0, 15))
        ttk.Button(form, text='生成方案', command=self.generate).pack(side='left')
        self.confirm = ttk.Button(form, text='确认方案并预留', command=self.confirm_proposal, state='disabled')
        self.confirm.pack(side='left', padx=8)
        ttk.Label(parent, textvariable=self.total, padding=(12, 4)).pack(anchor='w')
        ttk.Label(parent, textvariable=self.feedback, padding=12, wraplength=1050).pack(anchor='w')
        self.body = ttk.Frame(parent)
        self.body.pack(fill='both', expand=True)
        self.preview = ttk.Treeview(self.body, columns=('number', 'seller', 'date', 'amount'), show='headings')
        for key, label in zip(self.preview['columns'], ('发票号', '销售方', '日期', '金额（元）')):
            self.preview.heading(key, text=label)
            self.preview.column(key, width=180)
        self.preview.pack(fill='both', expand=True, padx=12, pady=12)
        for variable in (month, days, allowance):
            variable.trace_add('write', self.inputs_changed)
        self.inputs_changed()

    def inputs_changed(self, *args):
        from app import reimbursement_inputs, money
        self.proposal = None
        self.confirm.configure(state='disabled')
        if self.batch_view is None:
            self.preview.delete(*self.preview.get_children())
        try:
            _, days, allowance = reimbursement_inputs(self.month.get().strip(), self.days.get().strip(), self.allowance.get().strip())
            self.total.set(f'报销目标 ¥ {money(days * allowance)}')
            if self.batch_view is None:
                self.feedback.set('')
        except ValueError as error:
            self.total.set(str(error))

    def generate(self):
        from app import reimbursement_inputs, money
        if self.batch_view and (self.batch_view.busy or self.batch_view.pending_print is not None or self.batch_view.pending_action is not None):
            self.feedback.set('请先完成当前确认。')
            return
        try:
            inputs = reimbursement_inputs(self.month.get().strip(), self.days.get().strip(), self.allowance.get().strip())
            month, days, allowance = inputs
            rows = select_invoice_combination(database.list_invoices(), days * allowance)
            if not rows:
                raise ValueError('暂无可用发票，请到「发票库存」导入。')
        except (ValueError, sqlite3.Error) as error:
            self.proposal = None
            self.confirm.configure(state='disabled')
            self.feedback.set(str(error))
            return
        if self.batch_view:
            self.batch_view.window.destroy()
            self.batch_view = None
        self.preview.pack(fill='both', expand=True, padx=12, pady=12)
        self.preview.delete(*self.preview.get_children())
        for row in rows:
            self.preview.insert('', 'end', values=(row['invoice_number'], row['seller'], row['invoice_date'], money(row['total_amount_fen'])))
        total = sum(row['total_amount_fen'] for row in rows)
        difference = total - days * allowance
        self.total.set(f'{month} · 目标 ¥ {money(days * allowance)} · 自动凑票 {len(rows)} 张，共 ¥ {money(total)} · 差额 ¥ {money(difference)}')
        self.proposal = (inputs, rows)
        self.confirm.configure(state='normal')
        self.feedback.set('')
        self.stage.set('确认方案')

    def confirm_proposal(self):
        if not self.proposal:
            self.feedback.set('请先生成方案。')
            return
        inputs, rows = self.proposal
        try:
            batch_id = database.create_reimbursement_batch(*inputs, [row['invoice_number'] for row in rows])
        except (ValueError, sqlite3.Error, OverflowError) as error:
            self.proposal = None
            self.confirm.configure(state='disabled')
            self.feedback.set(f'未创建报销单：{error}。请重新生成方案。')
            return
        self.open_batch(batch_id)
        self.changed()

    def open_batch(self, batch_id):
        if self.batch_view and (self.batch_view.busy or self.batch_view.pending_print is not None or self.batch_view.pending_action is not None):
            self.feedback.set('请先完成当前确认。')
            return False
        if self.batch_view:
            self.batch_view.window.destroy()
        self.preview.pack_forget()
        self.total.set('')
        self.proposal = None
        self.confirm.configure(state='disabled')
        self.batch_view = BatchView(self.body, batch_id, self.batch_changed)
        self.feedback.set('')
        self.stage.set('打印 / 导出')
        return True


    def batch_changed(self):
        self.changed()
        state = self.batch_view.batch_status
        if state == 'used':
            self.stage.set('本单已完成报销')
        elif state == 'cancelled':
            self.batch_view.window.destroy()
            self.batch_view = None
            self.preview.delete(*self.preview.get_children())
            self.preview.pack(fill='both', expand=True, padx=12, pady=12)
            self.stage.set('本月报销')
            self.inputs_changed()
            self.feedback.set('已取消，记录已清空。')


class HistoryPanel:
    def __init__(self, parent, open_batch):
        self.open_batch = open_batch
        self.feedback = tk.StringVar(parent, '')
        bar = ttk.Frame(parent, padding=12)
        bar.pack(fill='x')
        ttk.Button(bar, text='继续办理 / 查看明细', command=self.open_selected).pack(side='left')
        ttk.Button(bar, text='刷新', command=self.reload).pack(side='left', padx=8)
        self.tree = ttk.Treeview(parent, columns=('id', 'month', 'count', 'target', 'total', 'status'), show='headings', selectmode='browse')
        for key, title in zip(self.tree['columns'], ('编号', '月份', '张数', '目标金额', '选票金额', '状态')):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=120)
        self.tree.pack(fill='both', expand=True, padx=12, pady=12)
        self.tree.bind('<Double-1>', lambda event: self.open_selected())
        ttk.Label(parent, textvariable=self.feedback, padding=12).pack(anchor='w')
        self.reload()

    def reload(self):
        from app import money
        try:
            rows = database.list_reimbursement_batches()
        except sqlite3.Error as error:
            self.feedback.set(f'读取报销记录失败：{error}')
            return
        selected = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        names = {'reserved': '待报销', 'used': '已报销', 'cancelled': '已取消'}
        for row in rows:
            self.tree.insert('', 'end', iid=str(row['id']), values=(row['id'], row['reimbursement_month'], row['invoice_count'], money(row['work_days'] * row['daily_allowance_fen']), money(row['total_amount_fen']), names[row['status']]))
        if selected and self.tree.exists(selected[0]):
            self.tree.selection_set(selected)

    def open_selected(self):
        selected = self.tree.selection()
        if not selected:
            self.feedback.set('请先选择一张报销单。')
            return
        self.open_batch(int(selected[0]))
