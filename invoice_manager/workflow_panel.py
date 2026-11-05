"""Embedded monthly reimbursement flow and history."""
import sqlite3
from collections import Counter
from tkinter import ttk, messagebox
import tkinter as tk

from invoice_manager import database
from invoice_manager.ui_widgets import checkbox_icons
from invoice_manager.invoice_files import display_invoice_number
from invoice_manager.batch_view import BatchView
from invoice_manager.reimbursement import create_batch
from invoice_manager.settings import save_settings
from invoice_manager.workdays import previous_month, workdays_in_month


class MonthlyWorkflow:
    def __init__(self, parent, month, days, allowance, changed):
        self.month, self.days, self.allowance = month, days, allowance
        self.changed = changed
        self.batch_view = None
        self.other_checked = set()
        self.other_rows = []
        self.feedback = tk.StringVar(parent, '')
        self.total = tk.StringVar(parent)
        self.company = tk.StringVar(parent, '')
        self.manual_target = tk.StringVar(parent, '')
        self.stage = tk.StringVar(parent, '本月报销')
        ttk.Label(parent, textvariable=self.stage, padding=(16, 14, 16, 2), style='Title.TLabel').pack(anchor='w')
        self.form = form = ttk.Frame(parent, padding=12)
        form.pack(fill='x')
        ttk.Label(form, text='购买方公司', style='Muted.TLabel').grid(row=0, column=0, sticky='w', padx=(0, 18), pady=(0, 4))
        self.company_select = ttk.Combobox(form, textvariable=self.company, state='readonly', width=28)
        self.company_select.grid(row=1, column=0, sticky='ew', padx=(0, 18))
        for column, (label, variable, width) in enumerate((('报销月份', month, 12), ('实际天数', days, 8), ('每日金额（元）', allowance, 12)), start=1):
            ttk.Label(form, text=label, style='Muted.TLabel').grid(row=0, column=column, sticky='w', padx=(0, 18), pady=(0, 4))
            ttk.Entry(form, textvariable=variable, width=width).grid(row=1, column=column, sticky='ew', padx=(0, 18))
        ttk.Label(form, text='直接输入报销金额（元，可选）', style='Muted.TLabel').grid(row=0, column=4, sticky='w', padx=(0, 18), pady=(0, 4))
        ttk.Entry(form, textvariable=self.manual_target, width=16).grid(row=1, column=4, sticky='ew', padx=(0, 18))
        self.generate_button = ttk.Button(form, text='生成报销单', command=self.generate)
        self.generate_button.grid(row=1, column=5, sticky='w')
        self.days_hint = tk.StringVar(parent)
        self.days_hint_label = ttk.Label(parent, textvariable=self.days_hint, padding=(12, 0))
        self.days_hint_label.pack(anchor='w')
        self.other_bar = ttk.Frame(parent, padding=(12, 2))
        self.other_bar.pack(fill='x')
        self.other_button = ttk.Button(self.other_bar, text='选择其他发票（0 张）', command=self.toggle_other_panel)
        self.other_button.pack(side='left')
        self.other_frame = ttk.Frame(parent, padding=(12, 4))
        toolbar = ttk.Frame(self.other_frame)
        toolbar.pack(fill='x', pady=(0, 4))
        self.other_select_all = ttk.Button(toolbar, text='全选', command=lambda: self.check_all_other(True))
        self.other_select_all.pack(side='left')
        ttk.Button(toolbar, text='取消全选', command=lambda: self.check_all_other(False)).pack(side='left', padx=6)
        table = ttk.Frame(self.other_frame)
        table.pack(fill='both', expand=True)
        self.other_icons = checkbox_icons(table)
        self.other_tree = ttk.Treeview(table, columns=('number', 'seller', 'amount'), show='tree headings', height=3, selectmode='browse')
        self.other_tree.heading('#0', text='勾选')
        self.other_tree.column('#0', width=60, minwidth=60, stretch=False)
        for key, title, width in [ ('number', '发票号', 210), ('seller', '销售方', 420), ('amount', '金额', 130)]:
            self.other_tree.heading(key, text=title)
            self.other_tree.column(key, width=width)
        self.other_tree.pack(side='left', fill='both', expand=True)
        scrollbar = ttk.Scrollbar(table, orient='vertical', command=self.other_tree.yview)
        scrollbar.pack(side='right', fill='y')
        self.other_tree.configure(yscrollcommand=scrollbar.set)
        self.other_tree.bind('<Button-1>', self.toggle_other)
        self.other_tree.bind('<space>', self.toggle_other_keyboard)
        self.total_label = ttk.Label(parent, textvariable=self.total, padding=(12, 8), style='Section.TLabel')
        self.total_label.pack(anchor='w')
        ttk.Label(parent, textvariable=self.feedback, padding=12, wraplength=1050).pack(anchor='w')
        self.body = ttk.Frame(parent)
        self.body.pack(fill='both', expand=True)
        self.empty = ttk.Label(self.body, text='填写报销信息后点击“生成报销单”。未完成的报销单可在报销历史中继续办理。', padding=12)
        self.empty.pack(anchor='w')
        for variable in (month, days, allowance, self.manual_target):
            variable.trace_add('write', self.inputs_changed)
        self.company.trace_add('write', self.company_changed)
        month.trace_add('write', self.update_workdays)
        self.update_workdays()
        self.refresh_other()
        self.inputs_changed()

    def company_changed(self, *args):
        self.refresh_other()
        self.inputs_changed()

    def refresh_companies(self, rows):
        counts = Counter(row['buyer'] for row in rows if row['status'] == 'available')
        companies = sorted({row['buyer'] for row in rows if row['buyer']})
        self.company_select.configure(values=companies)
        if self.company.get() not in companies:
            self.company.set(max(companies, key=lambda name: (counts[name], name)) if companies else '')

    def update_workdays(self, *args):
        try:
            count = workdays_in_month(self.month.get().strip())
        except ValueError as error:
            self.days.set('')
            self.days_hint.set(str(error))
            return
        self.days.set('' if count is None else str(count))
        self.days_hint.set(
            '按国家放假调休安排自动计算；也可填写直接报销金额覆盖天数 × 每日标准。'
            if count is not None else '该月份暂无内置的国家放假安排，请手动填写天数；也可直接输入报销金额。'
        )

    def refresh_other(self):
        from invoice_manager.app import money
        try:
            rows = database.list_invoices()
            self.refresh_companies(rows)
            self.other_rows = [row for row in rows if row['category'] == 'other' and row['status'] == 'available' and row['buyer'] == self.company.get()]
        except sqlite3.Error as error:
            self.feedback.set(str(error))
            return
        previous = set(self.other_checked)
        self.other_checked.intersection_update(row['invoice_number'] for row in self.other_rows)
        self.other_tree.delete(*self.other_tree.get_children())
        for row in self.other_rows:
            self.other_tree.insert('', 'end', iid=row['invoice_number'], image=self.other_icons[int(row['invoice_number'] in self.other_checked)], values=(display_invoice_number(row), row['seller'], money(row['total_amount_fen'])))
        self.other_button.configure(text=f'选择其他发票（{len(self.other_checked)} 张）')
        if previous != self.other_checked:
            self.inputs_changed()

    def toggle_other_panel(self):
        if self.other_frame.winfo_manager():
            self.other_frame.pack_forget()
        else:
            self.refresh_other()
            self.other_frame.pack(fill='x', before=self.total_label)
            if not self.other_rows:
                self.feedback.set('暂无待使用的其他发票，可在库存中导入或调整分类。')

    def check_all_other(self, checked):
        self.refresh_other()
        self.other_checked = {row['invoice_number'] for row in self.other_rows} if checked else set()
        self.refresh_other()
        self.inputs_changed()

    def toggle_other_number(self, number):
        if not any(row['invoice_number'] == number for row in self.other_rows):
            return
        if number in self.other_checked:
            self.other_checked.remove(number)
        else:
            self.other_checked.add(number)
        self.refresh_other()
        self.other_tree.selection_set(number)
        self.other_tree.focus(number)
        self.inputs_changed()

    def toggle_other(self, event):
        number = self.other_tree.identify_row(event.y)
        if number and self.other_tree.identify_column(event.x) == '#0':
            self.toggle_other_number(number)
            return 'break'

    def toggle_other_keyboard(self, event):
        number = self.other_tree.focus()
        if number:
            self.toggle_other_number(number)
        return 'break'

    def inputs_changed(self, *args):
        from invoice_manager.app import reimbursement_inputs, reimbursement_target, money
        if self.batch_view is not None:
            self.total.set('')
            return
        try:
            manual_target = reimbursement_target(self.manual_target.get())
            if manual_target is not None:
                reimbursement_inputs(self.month.get().strip(), '0', '0')
                meal_target = manual_target
            else:
                _, days, allowance = reimbursement_inputs(self.month.get().strip(), self.days.get().strip(), self.allowance.get().strip())
                meal_target = days * allowance
            other_total = sum(row['total_amount_fen'] for row in self.other_rows if row['invoice_number'] in self.other_checked)
            calculation = '直接金额' if manual_target is not None else '天数 × 每日标准'
            self.total.set(f'餐补目标 ¥{money(meal_target)}（{calculation}） · 其他 ¥{money(other_total)} · 合计应报销 ¥{money(meal_target + other_total)}')
        except ValueError:
            self.total.set('请填写月份、天数和每日标准；只报其他发票时天数填 0。')

    def generate(self):
        from invoice_manager.app import reimbursement_inputs, reimbursement_target
        if self.batch_view is not None:
            return
        self.generate_button.configure(state='disabled')
        try:
            manual_target = reimbursement_target(self.manual_target.get())
            if manual_target is not None:
                month, days, allowance = reimbursement_inputs(self.month.get().strip(), '0', '0')
            else:
                month, days, allowance = reimbursement_inputs(self.month.get().strip(), self.days.get().strip(), self.allowance.get().strip())
            batch_id = create_batch(month, days, allowance, self.other_checked, self.company.get(), manual_target)
        except (ValueError, sqlite3.Error, OverflowError) as error:
            self.feedback.set(f'未创建报销单：{error}')
            messagebox.showerror('未创建报销单', str(error), parent=self.body)
            return
        finally:
            self.generate_button.configure(state='normal')
        self.open_batch(batch_id)
        self.changed()
        try:
            save_settings(daily_allowance=self.allowance.get().strip())
        except OSError as error:
            messagebox.showwarning('设置未保存', f'报销单已创建，但每日标准未能保存：{error}', parent=self.body)

    def open_batch(self, batch_id):
        if self.batch_view is not None:
            if self.batch_view.batch_id == batch_id:
                return True
            messagebox.showwarning('有正在办理的报销单', '请先完成或取消当前报销单，再打开其他单据。', parent=self.body)
            return False
        self.empty.pack_forget()
        self.form.pack_forget()
        self.days_hint_label.pack_forget()
        self.other_bar.pack_forget()
        self.other_frame.pack_forget()
        self.total.set('')
        self.batch_view = BatchView(self.body, batch_id, self.batch_changed)
        self.feedback.set('')
        self.stage.set('办理报销')
        return True

    def reset(self, feedback):
        if self.batch_view:
            self.batch_view.window.destroy()
            self.batch_view = None
        self.other_checked.clear()
        self.other_frame.pack_forget()
        self.month.set(previous_month())
        self.update_workdays()
        self.manual_target.set('')
        self.refresh_other()
        self.form.pack(fill='x', before=self.total_label)
        self.days_hint_label.pack(anchor='w', before=self.total_label)
        self.other_bar.pack(fill='x', before=self.total_label)
        self.empty.pack(anchor='w')
        self.stage.set('本月报销')
        self.inputs_changed()
        self.feedback.set(feedback)

    def batch_changed(self):
        state = self.batch_view.batch_status
        if state == 'used':
            self.reset('报销已完成，记录已存入历史。')
        elif state == 'cancelled':
            self.reset('已取消，记录已清空，发票已释放。')
        self.changed()


class HistoryPanel:
    def __init__(self, parent, open_batch):
        self.open_batch = open_batch
        self.feedback = tk.StringVar(parent, '')
        bar = ttk.Frame(parent, padding=(16, 14, 16, 10))
        bar.pack(fill='x')
        ttk.Label(bar, text='报销历史', style='Title.TLabel').pack(side='left')
        ttk.Button(bar, text='继续办理 / 查看明细', command=self.open_selected).pack(side='right')
        ttk.Button(bar, text='刷新', command=self.reload).pack(side='right', padx=8)
        table = ttk.Frame(parent, padding=(16, 0, 16, 0))
        table.pack(fill='both', expand=True)
        self.tree = ttk.Treeview(table, columns=('id', 'month', 'buyer', 'count', 'target', 'total', 'status'), show='headings', selectmode='browse')
        for key, title in zip(self.tree['columns'], ('编号', '月份', '购买方公司', '张数', '目标金额', '选票金额', '状态')):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=210 if key == 'buyer' else 105)
        self.tree.pack(side='left', fill='both', expand=True)
        scrollbar = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        scrollbar.pack(side='right', fill='y')
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind('<Double-1>', lambda event: self.open_selected())
        ttk.Label(parent, textvariable=self.feedback, padding=(16, 8)).pack(anchor='w')
        self.reload()

    def reload(self):
        from invoice_manager.app import money
        try:
            rows = database.list_reimbursement_batches()
        except sqlite3.Error as error:
            self.feedback.set(f'读取报销记录失败：{error}')
            return
        selected = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        names = {'reserved': '待报销', 'used': '已报销', 'cancelled': '已取消'}
        for row in rows:
            self.tree.insert('', 'end', iid=str(row['id']), values=(row['id'], row['reimbursement_month'], row['buyer'] or '', row['invoice_count'], money(row['target_amount_fen']), money(row['total_amount_fen']), names[row['status']]))
        if selected and self.tree.exists(selected[0]):
            self.tree.selection_set(selected)

    def open_selected(self):
        selected = self.tree.selection()
        if not selected:
            self.feedback.set('请先选择一张报销单。')
            return
        self.open_batch(int(selected[0]))
