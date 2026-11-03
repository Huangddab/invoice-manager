import gc
from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch

import fitz
from invoice_manager import database
from invoice_manager.app import InvoiceApp
from invoice_manager.read_invoice import import_invoices


TEXT = '''深圳电子普通发票
发票代码：100000000001
发票号码：00000001
开票日期：2026 年 11 月 03 日
   名    称：示例购买公司      密    123456
价税合计（大写） 玖佰元整 （小写）¥900.00
   名    称：示例餐饮公司
'''


def make_pdf(path, text='', encrypted=False):
    with fitz.open() as doc:
        page = doc.new_page(width=600, height=400)
        if text:
            page.insert_text((20, 25), text, fontname='china-s', fontsize=10)
        if encrypted:
            doc.save(path, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw='owner', user_pw='password')
        else:
            doc.save(path)


class ImportResultTests(unittest.TestCase):
    def setUp(self):
        gc.collect()
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.old_db = database.DB_PATH
        database.DB_PATH = self.directory / 'invoices.db'
        database.init_db()

    def tearDown(self):
        database.DB_PATH = self.old_db
        self.temp.cleanup()

    def test_distinct_failure_reasons_full_paths_and_successes(self):
        valid = self.directory / 'valid.pdf'
        duplicate = self.directory / 'duplicate.pdf'
        blank = self.directory / 'blank.pdf'
        encrypted = self.directory / 'encrypted.pdf'
        missing_fields = self.directory / 'missing-amount.pdf'
        broken = self.directory / 'broken.pdf'
        absent = self.directory / 'absent.pdf'
        make_pdf(valid, TEXT)
        duplicate.write_bytes(valid.read_bytes())
        make_pdf(blank)
        make_pdf(encrypted, TEXT, encrypted=True)
        make_pdf(missing_fields, TEXT.replace('（小写）¥900.00', ''))
        broken.write_bytes(b'broken pdf')
        result = import_invoices([valid, duplicate, blank, encrypted, missing_fields, broken, absent], 'other')
        self.assertEqual((result['added'], result['duplicates'], len(result['errors'])), (1, 1, 5))
        self.assertEqual(len(result['items']), 7)
        errors = {Path(path).name: reason for path, reason in result['errors']}
        self.assertIn('OCR', errors[blank.name])
        self.assertIn('需要密码', errors[encrypted.name])
        self.assertIn('价税合计', errors[missing_fields.name])
        self.assertIn('不存在', errors[absent.name])
        self.assertTrue(errors[broken.name])
        self.assertTrue(all(Path(path).is_absolute() for path, _ in result['errors']))
        self.assertEqual(database.list_invoices()[0]['category'], 'other')

    def test_permission_failure_explains_access_problem(self):
        source = self.directory / 'invoice.pdf'
        make_pdf(source, TEXT)
        with patch('invoice_manager.read_invoice.shutil.copyfile', side_effect=PermissionError('access denied')):
            result = import_invoices([source])
        self.assertEqual(result['added'], 0)
        self.assertIn('权限', result['errors'][0][1])
        self.assertEqual(database.list_invoices(), [])

    def test_scan_directory_access_failure_is_visible(self):
        root, app = self.make_app()
        with patch('invoice_manager.app.filedialog.askdirectory', return_value=str(self.directory)), \
             patch('pathlib.Path.iterdir', side_effect=PermissionError('access denied')), \
             patch('tkinter.messagebox.showerror') as dialog:
            app.scan_folder('meal')
            dialog.assert_called_once()
        self.assertIn('access denied', app.feedback.get())
        self.assertFalse(app.importing)

    def make_app(self):
        root = tk.Tk()
        self.addCleanup(root.destroy)
        return root, InvoiceApp(root)

    def wait_import(self, root, app):
        deadline = time.monotonic() + 5
        while app.importing and time.monotonic() < deadline:
            root.update()
            time.sleep(0.01)
        self.assertFalse(app.importing)
        root.update()

    def test_failure_popup_reopen_copy_and_retry_only_failed_files(self):
        valid = self.directory / 'valid.pdf'
        broken = self.directory / 'broken.pdf'
        make_pdf(valid, TEXT)
        broken.write_bytes(b'broken')
        root, app = self.make_app()
        app.start_import([valid, broken], 'other')
        self.wait_import(root, app)
        dialog = app.import_result_window
        self.assertTrue(dialog.winfo_exists())
        self.assertEqual(dialog.items[0][1], '失败')
        self.assertIn(str(broken), dialog.details.get('1.0', 'end'))
        dialog.copy_errors()
        self.assertIn(str(broken), root.clipboard_get())
        dialog.destroy()
        app.import_details_button.invoke()
        self.assertTrue(app.import_result_window.winfo_exists())
        # Repair only the failed file, keeping a distinct invoice identity.
        broken.unlink()
        make_pdf(broken, TEXT.replace('00000001', '00000002'))
        app.import_result_window.retry_errors()
        self.wait_import(root, app)
        self.assertEqual((app.last_import_result['added'], app.last_import_result['duplicates']), (1, 0))
        self.assertEqual(app.last_import_result['errors'], [])
        self.assertEqual(len(database.list_invoices()), 2)
        self.assertTrue(all(row['category'] == 'other' for row in database.list_invoices()))

    def test_retry_missing_file_explains_and_does_not_import(self):
        from invoice_manager.import_results import ImportResultWindow
        root, app = self.make_app()
        missing = self.directory / 'missing.pdf'
        result = {'summary': '失败 1 个', 'category': 'meal', 'errors': [(str(missing), '文件不存在')]}
        dialog = ImportResultWindow(root, result, app.start_import)
        with patch('tkinter.messagebox.showwarning') as warning:
            dialog.retry_errors()
            warning.assert_called_once()
        self.assertFalse(app.importing)
        self.assertTrue(dialog.winfo_exists())
