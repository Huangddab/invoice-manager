"""Readable per-file import outcomes and retry of failed source files."""
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk


class ImportResultWindow(tk.Toplevel):
    def __init__(self, parent, result, retry):
        super().__init__(parent)
        self.result, self.retry = result, retry
        self.title('发票导入结果 · 失败原因')
        self.geometry('1000x540')
        self.minsize(720, 400)
        self.transient(parent)
        ttk.Label(self, text=result['summary'], padding=12).pack(anchor='w')
        frame = ttk.Frame(self, padding=(12, 0))
        frame.pack(fill='both', expand=True)
        self.tree = ttk.Treeview(frame, columns=('file', 'status', 'reason'), show='headings', selectmode='browse')
        for key, title, width in [('file', '文件', 240), ('status', '结果', 65), ('reason', '原因及处理建议', 620)]:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, minwidth=60)
        scrollbar = ttk.Scrollbar(frame, orient='vertical', command=self.tree.yview)
        horizontal = ttk.Scrollbar(frame, orient='horizontal', command=self.tree.xview)
        self.tree.configure(yscrollcommand=scrollbar.set, xscrollcommand=horizontal.set)
        self.tree.grid(row=0, column=0, sticky='nsew')
        scrollbar.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.items = sorted(result.get('items') or [(path, '失败', reason) for path, reason in result['errors']],
                            key=lambda item: item[1] != '失败')
        for index, (path, status, reason) in enumerate(self.items):
            self.tree.insert('', 'end', iid=str(index), values=(Path(path).name, status, reason))
        self.details = tk.Text(self, height=5, wrap='word', state='disabled')
        self.details.pack(fill='x', padx=12, pady=8)
        self.tree.bind('<<TreeviewSelect>>', self.show_selected)
        controls = ttk.Frame(self, padding=12)
        controls.pack(fill='x')
        ttk.Button(controls, text='复制失败明细', command=self.copy_errors).pack(side='left')
        ttk.Button(controls, text='重试失败项', command=self.retry_errors,
                   state='normal' if result['errors'] else 'disabled').pack(side='left', padx=8)
        ttk.Button(controls, text='关闭', command=self.destroy).pack(side='right')
        if self.items:
            self.tree.selection_set('0')
            self.show_selected()
        self.lift()

    def show_selected(self, event=None):
        selection = self.tree.selection()
        if not selection:
            return
        path, status, reason = self.items[int(selection[0])]
        self.details.configure(state='normal')
        self.details.delete('1.0', 'end')
        self.details.insert('1.0', f'完整路径：{path}\n结果：{status}\n{reason}')
        self.details.configure(state='disabled')

    def copy_errors(self):
        self.clipboard_clear()
        self.clipboard_append('\n\n'.join(f'{path}\n{reason}' for path, reason in self.result['errors']))

    def retry_errors(self):
        paths = [Path(path) for path, _ in self.result['errors'] if Path(path).is_file()]
        missing = len(self.result['errors']) - len(paths)
        if missing:
            messagebox.showwarning('部分文件无法重试', f'{missing} 个文件已不存在，请检查本次详情中的完整路径。', parent=self)
        if paths:
            self.retry(paths, self.result['category'])
            self.destroy()
