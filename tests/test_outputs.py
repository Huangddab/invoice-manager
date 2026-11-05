import gc
import hashlib
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import fitz
from invoice_manager import database
from invoice_manager.batch_view import BatchView
from invoice_manager.invoice_files import batch_export_name, export_name, export_folder, original_path, prepare_print_pdf, print_command, submit_print
from invoice_manager.read_invoice import import_invoices


class OutputTests(unittest.TestCase):
    def setUp(self):
        errors = patch('tkinter.messagebox.showerror')
        errors.start()
        self.addCleanup(errors.stop)
        # Collect prior destroyed Tk roots on the UI thread before worker tests.
        gc.collect()
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
        gc.collect()
        self.temp.cleanup()

    def test_printed_invoices_start_unchecked_and_can_be_selected(self):
        database.update_printed_batch([self.number], True)
        _, view = self.make_view()
        self.assertEqual(view.checked, set())
        view.check_all(True)
        self.assertEqual(view.checked, {self.number})
        view.check_unprinted()
        self.assertEqual(view.checked, set())

    def test_confirmed_print_is_unchecked(self):
        _, view = self.make_view()
        self.assertEqual(view.checked, {self.number})
        with patch('tkinter.messagebox.askyesno', return_value=True):
            view.confirm_print_result([self.number])
        self.assertEqual(view.checked, set())
        self.assertEqual(database.list_invoices()[0]['is_printed'], 1)

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
        root = self.directory / 'exports'
        destination = export_folder(self.rows, root, '2026-11', 90000)
        self.assertEqual(destination.name, '2026-11应报销：900实际报销：428差额-472')
        self.assertEqual((destination / export_name(self.rows[0])).read_bytes(), self.pdf.read_bytes())
        again = export_folder(self.rows, root, '2026-11', 90000)
        self.assertEqual(again.name, destination.name + ' (2)')
        self.assertEqual((destination / export_name(self.rows[0])).read_bytes(), self.pdf.read_bytes())
        row = database.list_invoices()[0]
        self.assertEqual((row['status'], row['is_printed']), ('reserved', 0))
        with self.assertRaises(ValueError):
            export_folder([], root, '2026-11', 90000)

    def test_missing_file_and_failed_copy_leave_no_partial_export(self):
        root = self.directory / 'exports'
        root.mkdir()
        old = root / 'existing'
        old.mkdir()
        (old / 'keep.pdf').write_bytes(b'keep')
        with self.assertRaises(FileNotFoundError):
            export_folder([*self.rows, dict(self.rows[0], invoice_number='missing', file_path='missing.pdf')], root, '2026-11', 90000)
        with patch('invoice_manager.invoice_files.shutil.copyfile', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError, 'disk full'):
                export_folder(self.rows, root, '2026-11', 90000)
        self.assertEqual(list(root.iterdir()), [old])
        self.assertEqual((old / 'keep.pdf').read_bytes(), b'keep')

    def test_second_copy_failure_cleans_first_copy(self):
        import shutil
        root = self.directory / 'exports'
        original_copy = shutil.copyfile
        second = dict(self.rows[0], invoice_number='second', invoice_serial='second')
        calls = []
        def copy(source, destination):
            calls.append(destination)
            if len(calls) == 2:
                raise OSError('disk full')
            return original_copy(source, destination)
        with patch('invoice_manager.invoice_files.shutil.copyfile', side_effect=copy):
            with self.assertRaisesRegex(OSError, 'disk full'):
                export_folder([*self.rows, second], root, '2026-11', 90000)
        self.assertEqual(list(root.iterdir()), [])

    def test_print_confirmation_uses_last_submitted_selection(self):
        root, view = self.make_view()
        with patch('invoice_manager.batch_view.print_command', return_value=['mock']), \
             patch('invoice_manager.batch_view.submit_print'), \
             patch('invoice_manager.batch_view.messagebox.askyesno', return_value=True) as dialog:
            view.print_checked()
            view.check_all(False)
            deadline = time.monotonic() + 5
            while view.busy and time.monotonic() < deadline:
                root.update()
                time.sleep(0.01)
            dialog.assert_called_once()
        self.assertEqual(database.list_invoices()[0]['is_printed'], 1)

    def test_print_copy_preserves_original_size_text_and_annotations(self):
        source = original_path(self.rows[0])
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        destination = self.directory / 'print.pdf'
        prepare_print_pdf(self.rows, destination)
        with fitz.open(destination) as document, fitz.open(source) as original:
            self.assertEqual(len(document), len(original))
            for page, original_page in zip(document, original):
                self.assertEqual(page.rect, original_page.rect)
                self.assertEqual(page.mediabox, original_page.mediabox)
                self.assertEqual(page.cropbox, original_page.cropbox)
                self.assertEqual(page.rotation, original_page.rotation)
                self.assertEqual(page.get_text(), original_page.get_text())
                # Exact rendered pixels catch changed placement, annotations and colors.
                self.assertEqual(page.get_pixmap().samples, original_page.get_pixmap().samples)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)
        self.assertEqual(database.list_invoices()[0]['is_printed'], 0)

    def test_mixed_sizes_rotation_and_multiple_pages_are_preserved(self):
        multi = self.directory / 'multi.pdf'
        with fitz.open() as document:
            first = document.new_page(width=300, height=200)
            first.insert_text((30, 40), 'Vector text', color=(1, 0, 0))
            first.set_cropbox(fitz.Rect(10, 10, 290, 190))
            first.set_rotation(90)
            first.add_text_annot((50, 60), 'Keep annotation')
            document.new_page(width=612, height=792)
            document.save(multi)
        destination = self.directory / 'merged.pdf'
        prepare_print_pdf([dict(self.rows[0], file_path=str(multi)), *self.rows], destination)
        with fitz.open(multi) as original, fitz.open(destination) as document:
            self.assertEqual(len(document), 3)
            for index in range(2):
                self.assertEqual(document[index].rect, original[index].rect)
                self.assertEqual(document[index].cropbox, original[index].cropbox)
                self.assertEqual(document[index].rotation, original[index].rotation)
                self.assertEqual(document[index].get_pixmap().samples, original[index].get_pixmap().samples)
            self.assertIn('Vector text', document[0].get_text())

    def test_export_folder_name_and_remembered_destination(self):
        rows = [dict(self.rows[0], total_amount_fen=89900)]
        self.assertEqual(batch_export_name('2026-11', 90000, rows), '2026-11应报销：900实际报销：899差额-1')
        self.assertNotIn(':', batch_export_name('2026-11', 90000, rows))
        _, view = self.make_view()
        root = self.directory / 'chosen-root'
        with patch('invoice_manager.batch_view.filedialog.askdirectory', return_value=str(root)) as dialog:
            view.export()
            dialog.assert_called_once()
        self.assertEqual(view.last_export.parent, root)
        with patch('invoice_manager.batch_view.filedialog.askdirectory') as dialog:
            view.export()
            dialog.assert_not_called()
        self.assertTrue(view.last_export.name.endswith(' (2)'))
        with patch('invoice_manager.app.open_document') as opener:
            view.open_export()
            opener.assert_called_once_with(view.last_export)

    def test_windows_opens_pdf_in_edge_without_rasterizing(self):
        destination = self.directory / 'print with spaces.pdf'
        with patch('invoice_manager.invoice_files.sys.platform', 'win32'), \
             patch('invoice_manager.invoice_files.edge_executable', return_value='msedge.exe'):
            prepare_print_pdf(self.rows, destination)
            command = print_command(destination)
            self.assertEqual(command, ['msedge.exe', '--new-window', destination.resolve().as_uri()])
            self.assertFalse((self.directory / 'native-print-pages').exists())
            with patch('invoice_manager.invoice_files.subprocess.Popen') as launch:
                submit_print(command)
                launch.assert_called_once_with(command)
            with patch('invoice_manager.invoice_files.subprocess.Popen', side_effect=OSError('launch failed')):
                with self.assertRaises(OSError):
                    submit_print(command)
        self.assertEqual(database.list_invoices()[0]['is_printed'], 0)

    def test_missing_edge_reports_error(self):
        with patch('invoice_manager.invoice_files.sys.platform', 'win32'), \
             patch('invoice_manager.invoice_files.shutil.which', return_value=None), \
             patch('invoice_manager.invoice_files.os.environ', {}):
            with self.assertRaisesRegex(FileNotFoundError, 'Edge'):
                print_command('print.pdf')

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
        def answer(*args, **kwargs):
            self.assertEqual(database.list_invoices()[0]['is_printed'], 0)
            return success_confirmation
        with patch('invoice_manager.batch_view.print_command', return_value=['mock']), \
             patch('invoice_manager.batch_view.submit_print', side_effect=submit_error), \
             patch('invoice_manager.batch_view.messagebox.askyesno', side_effect=answer) as dialog:
            view.print_checked()
            deadline = time.monotonic() + 5
            while view.busy and time.monotonic() < deadline:
                root.update()
                time.sleep(0.01)
            self.assertFalse(view.busy)
            self.assertIsNone(view.pending_print)
            if submit_error is None:
                dialog.assert_called_once()
            else:
                dialog.assert_not_called()
            return dialog.called

    def test_print_requires_actual_success_confirmation(self):
        self.run_print(False)
        self.assertEqual(database.list_invoices()[0]['is_printed'], 0)
        self.run_print(True)
        self.assertEqual(database.list_invoices()[0]['is_printed'], 1)
        self.assertEqual(database.list_invoices()[0]['status'], 'reserved')

    def test_automatic_prompt_no_keeps_print_state(self):
        root, view = self.make_view()
        with patch('invoice_manager.batch_view.print_command', return_value=['mock']), \
             patch('invoice_manager.batch_view.submit_print'), \
             patch('invoice_manager.batch_view.messagebox.askyesno', return_value=False) as dialog:
            view.print_checked()
            deadline = time.monotonic() + 5
            while view.busy and time.monotonic() < deadline:
                root.update()
                time.sleep(0.01)
            self.assertEqual(database.list_invoices()[0]['is_printed'], 0)
            dialog.assert_called_once()
            self.assertFalse(hasattr(view, 'mark_print_button'))

    def test_print_failure_never_requests_success_or_marks_printed(self):
        has_confirmation = self.run_print(True, RuntimeError('cancelled or failed'))
        self.assertFalse(has_confirmation)
        self.assertEqual(database.list_invoices()[0]['is_printed'], 0)

    def test_cancel_print_leaves_state(self):
        _, view = self.make_view()
        with patch('invoice_manager.batch_view.print_command', side_effect=FileNotFoundError('no printer')), \
             patch('invoice_manager.batch_view.submit_print') as submit:
            view.print_checked()
            submit.assert_not_called()
        self.assertFalse(view.busy)
        self.assertEqual(database.list_invoices()[0]['is_printed'], 0)

    def test_popup_reimbursement_confirmation_and_cancel(self):
        _, view = self.make_view()
        with patch('invoice_manager.batch_view.messagebox.askyesno', return_value=False) as dialog:
            view.request_finish('used')
            dialog.assert_called_once()
        self.assertEqual(database.list_invoices()[0]['status'], 'reserved')
        with patch('invoice_manager.batch_view.messagebox.askyesno', return_value=True):
            view.request_finish('used')
        self.assertEqual(database.list_invoices()[0]['status'], 'used')
        self.assertEqual(str(view.finish_button['state']), 'disabled')

    def test_popup_release_reservation(self):
        _, view = self.make_view()
        with patch('invoice_manager.batch_view.messagebox.askyesno', return_value=False):
            view.request_finish('cancelled')
        self.assertEqual(database.list_invoices()[0]['status'], 'reserved')
        self.assertEqual(len(database.list_reimbursement_batches()), 1)
        with patch('invoice_manager.batch_view.messagebox.askyesno', return_value=True):
            view.request_finish('cancelled')
        self.assertEqual(database.list_invoices()[0]['status'], 'available')
        self.assertEqual(database.list_reimbursement_batches(), [])
        self.assertEqual(database.reimbursement_invoices(self.batch), [])

    def test_open_original_works_immediately_without_clicking_row(self):
        _, view = self.make_view()
        self.assertEqual(view.tree.selection(), (self.number,))
        with patch('invoice_manager.app.open_document') as opener:
            view.open_pdf()
        opener.assert_called_once_with(original_path(self.rows[0]))
        self.assertIn('正在打开 PDF', view.feedback.get())
        view.check_all(False)
        with patch('invoice_manager.app.open_document') as opener:
            view.open_pdf()
        opener.assert_called_once()

    def test_open_missing_file_and_empty_selection_report_errors(self):
        _, view = self.make_view()
        view.tree.selection_remove(*view.tree.selection())
        view.open_pdf()
        self.assertIn('请先点击', view.feedback.get())
        view.tree.selection_set(self.number)
        original_path(self.rows[0]).unlink()
        with patch('invoice_manager.app.open_document') as opener:
            view.open_pdf()
        opener.assert_not_called()
        self.assertIn('无法打开', view.feedback.get())

    def test_windows_missing_default_reader_has_actionable_error(self):
        from invoice_manager.app import open_document
        error = OSError('no association')
        error.winerror = 1155
        with patch('invoice_manager.app.sys.platform', 'win32'), patch('invoice_manager.app.os.startfile', create=True, side_effect=error):
            with self.assertRaisesRegex(OSError, '默认打开方式'):
                open_document(self.pdf)

    def test_no_checkboxes_selected_reports_inline(self):
        _, view = self.make_view()
        view.check_all(False)
        view.export()
        self.assertIn('请先勾选', view.feedback.get())
        view.print_checked()
        self.assertEqual(database.list_invoices()[0]['is_printed'], 0)
