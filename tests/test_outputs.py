import base64
import hashlib
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

import fitz
import database
from batch_view import BatchView
from invoice_files import batch_export_name, prepare_windows_images, export_name, export_zip, original_path, prepare_print_pdf, print_command, submit_print
from read_invoice import import_invoices


class OutputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.old_db = database.DB_PATH
        database.DB_PATH = self.directory / 'invoices.db'
        database.init_db()
        self.pdf = next((Path(__file__).resolve().parents[1] / 'invoices').glob('26952000004515245716*.pdf'))
        import_invoices([self.pdf])
        self.rows = database.list_invoices()
        self.number = self.rows[0]['invoice_number']
        self.batch = database.create_reimbursement_batch('2026-10', 20, 2500, [self.number])

    def tearDown(self):
        database.DB_PATH = self.old_db
        self.temp.cleanup()

    def test_managed_original_and_renamed_duplicate(self):
        managed = original_path(self.rows[0])
        self.assertEqual(managed.parent, self.directory / 'managed_pdfs')
        self.assertEqual(managed.read_bytes(), self.pdf.read_bytes())
        renamed = self.directory / 'renamed.pdf'
        renamed.write_bytes(self.pdf.read_bytes())
        self.assertEqual(import_invoices([renamed])['duplicates'], 1)
        renamed.unlink()
        self.assertTrue(managed.is_file())
        self.assertEqual(len(database.list_invoices()), 1)

    def test_company_names_and_amounts(self):
        for value, expected in [(42800, '428'), (40000, '400'), (42850, '428.5'), (0, '0'), (5, '0.05')]:
            row = dict(self.rows[0], seller='广酒（深圳）餐饮管理有限公司', total_amount_fen=value)
            self.assertEqual(export_name(row), f'{self.number}_广酒（深圳）餐饮管理有限公司_{expected}.pdf')
        self.assertNotIn('/', export_name(dict(self.rows[0], seller='公司/餐饮')))

    def test_export_original_bytes_and_no_state_change(self):
        destination = self.directory / 'batch.zip'
        self.assertEqual(export_zip(self.rows, destination), 1)
        with zipfile.ZipFile(destination) as archive:
            self.assertEqual(archive.namelist(), [export_name(self.rows[0])])
            self.assertEqual(archive.read(archive.namelist()[0]), self.pdf.read_bytes())
        row = database.list_invoices()[0]
        self.assertEqual((row['status'], row['is_printed']), ('reserved', 0))
        with self.assertRaises(ValueError):
            export_zip([], destination)

    def test_missing_file_preserves_existing_export(self):
        destination = self.directory / 'batch.zip'
        destination.write_bytes(b'previous export')
        with self.assertRaises(FileNotFoundError):
            export_zip([dict(self.rows[0], invoice_number='missing', file_path='missing.pdf')], destination)
        self.assertEqual(destination.read_bytes(), b'previous export')

    def test_print_copy_a5_landscape_grayscale_and_original_unchanged(self):
        source = original_path(self.rows[0])
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        destination = self.directory / 'print.pdf'
        prepare_print_pdf(self.rows, destination)
        with fitz.open(destination) as document:
            self.assertEqual(len(document), 1)
            page = document[0]
            self.assertAlmostEqual(page.rect.width, 210 / 25.4 * 72, places=2)
            self.assertAlmostEqual(page.rect.height, 148 / 25.4 * 72, places=2)
            images = page.get_images()
            self.assertEqual(len(images), 1)
            self.assertEqual(fitz.Pixmap(document, images[0][0]).n, 1)
            bounds = page.get_image_rects(images[0][0])[0]
            self.assertTrue(page.rect.contains(bounds))
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)
        self.assertEqual(database.list_invoices()[0]['is_printed'], 0)

    def test_multipage_rejected_before_submission(self):
        multi = self.directory / 'multi.pdf'
        with fitz.open() as document:
            document.new_page()
            document.new_page()
            document.save(multi)
        with self.assertRaises(ValueError):
            prepare_print_pdf([dict(self.rows[0], file_path=str(multi))], self.directory / 'print.pdf')

    def test_export_package_name(self):
        rows = [dict(self.rows[0], total_amount_fen=9000)]
        self.assertEqual(batch_export_name('2026-10', 10000, rows), '2026-10应报销：100实际报销：90差额-10.zip')
        self.assertEqual(batch_export_name('2026-10', 9000, rows), '2026-10应报销：90实际报销：90差额0.zip')
        self.assertEqual(batch_export_name('2026-10', 8000, rows), '2026-10应报销：80实际报销：90差额10.zip')
        self.assertNotIn(':', batch_export_name('2026-10', 10000, rows))
        _, view = self.make_view()
        with patch('batch_view.filedialog.asksaveasfilename', return_value='') as dialog:
            view.export()
        self.assertEqual(dialog.call_args.kwargs['initialfile'], '2026-10应报销：500实际报销：428差额-72.zip')

    def test_windows_builtin_print_command_and_rendered_images(self):
        destination = self.directory / 'print.pdf'
        prepare_print_pdf(self.rows, destination)
        images = prepare_windows_images(destination)
        self.assertEqual(len(list(images.glob('*.png'))), 1)
        pixmap = fitz.Pixmap(str(next(images.glob('*.png'))))
        self.assertEqual(pixmap.n, 1)
        with patch('invoice_files.sys.platform', 'win32'), patch('invoice_files.shutil.which', return_value=None):
            command = print_command(destination)
        self.assertEqual(command[0], 'powershell.exe')
        self.assertIn('-STA', command)
        script = base64.b64decode(command[-1]).decode('utf-16-le')
        self.assertIn('System.Windows.Forms.PrintDialog', script)
        self.assertIn('PaperKind]::A5', script)
        self.assertIn('Landscape = $true', script)
        self.assertIn('Color = $false', script)
        self.assertIn('exit 2', script)
        with patch('invoice_files.subprocess.run') as runner:
            runner.return_value.returncode = 2
            with self.assertRaisesRegex(RuntimeError, '已取消打印'):
                submit_print(command)
        self.assertEqual(database.list_invoices()[0]['is_printed'], 0)

    def test_windows_print_settings_and_failed_command(self):
        with patch('invoice_files.sys.platform', 'win32'):
            command = print_command('print.pdf', 'SumatraPDF.exe')
        self.assertIn('paper=A5,landscape,monochrome,fit', command)
        self.assertIn('-print-dialog', command)
        with patch('invoice_files.subprocess.run') as runner:
            runner.return_value.returncode = 1
            runner.return_value.stderr = 'printer unavailable'
            with self.assertRaises(RuntimeError):
                submit_print(command)
        self.assertEqual(database.list_invoices()[0]['is_printed'], 0)

    def make_view(self):
        import tkinter as tk
        root = tk.Tk()
        self.addCleanup(root.destroy)
        view = BatchView(root, self.batch)
        root.update()
        return root, view

    def test_checkboxes_independent_from_reservation(self):
        _, view = self.make_view()
        self.assertEqual(len(view.selected_rows()), 1)
        view.check_all(False)
        self.assertEqual(view.selected_rows(), [])
        self.assertEqual(database.list_invoices()[0]['status'], 'reserved')
        view.toggle_number(self.number)
        self.assertEqual(len(view.selected_rows()), 1)

    def run_print(self, success_confirmation, submit_error=None):
        root, view = self.make_view()
        with patch('batch_view.print_command', return_value=['mock']), \
             patch('batch_view.submit_print', side_effect=submit_error):
            view.print_checked()
            deadline = time.monotonic() + 5
            while view.busy and time.monotonic() < deadline:
                root.update()
                time.sleep(0.01)
            self.assertFalse(view.busy)
            has_confirmation = view.pending_print is not None
            # Submission alone must never mark a new invoice printed.
            self.assertEqual(database.list_invoices()[0]['is_printed'], 0)
            if success_confirmation:
                view.confirm_pending()
            else:
                view.dismiss_pending()
            return has_confirmation

    def test_print_requires_actual_success_confirmation(self):
        self.run_print(False)
        self.assertEqual(database.list_invoices()[0]['is_printed'], 0)
        self.run_print(True)
        self.assertEqual(database.list_invoices()[0]['is_printed'], 1)
        self.assertEqual(database.list_invoices()[0]['status'], 'reserved')

    def test_print_failure_never_requests_success_or_marks_printed(self):
        has_confirmation = self.run_print(True, RuntimeError('cancelled or failed'))
        self.assertFalse(has_confirmation)
        self.assertEqual(database.list_invoices()[0]['is_printed'], 0)

    def test_cancel_print_leaves_state(self):
        _, view = self.make_view()
        with patch('batch_view.print_command', side_effect=FileNotFoundError('no printer')), \
             patch('batch_view.submit_print') as submit:
            view.print_checked()
            submit.assert_not_called()
        self.assertFalse(view.busy)
        self.assertEqual(database.list_invoices()[0]['is_printed'], 0)

    def test_inline_reimbursement_confirmation_and_cancel(self):
        _, view = self.make_view()
        view.request_finish('used')
        self.assertEqual(database.list_invoices()[0]['status'], 'reserved')
        view.dismiss_pending()
        self.assertEqual(database.list_invoices()[0]['status'], 'reserved')
        view.request_finish('used')
        view.confirm_pending()
        self.assertEqual(database.list_invoices()[0]['status'], 'used')
        self.assertEqual(str(view.finish_button['state']), 'disabled')

    def test_inline_release_reservation(self):
        _, view = self.make_view()
        view.request_finish('cancelled')
        self.assertEqual(database.list_invoices()[0]['status'], 'reserved')
        view.confirm_pending()
        self.assertEqual(database.list_invoices()[0]['status'], 'available')

    def test_no_checkboxes_selected_reports_inline(self):
        _, view = self.make_view()
        view.check_all(False)
        view.export()
        self.assertIn('请先勾选', view.feedback.get())
        view.print_checked()
        self.assertEqual(database.list_invoices()[0]['is_printed'], 0)
