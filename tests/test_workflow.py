import gc
import contextlib
import importlib
import io
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from invoice_manager import database
from invoice_manager.app import InvoiceApp, filter_invoices, reimbursement_inputs
from invoice_manager.read_invoice import import_invoices


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        errors = patch('tkinter.messagebox.showerror')
        errors.start()
        self.addCleanup(errors.stop)
        # Collect prior destroyed Tk roots on the UI thread before worker tests.
        gc.collect()
        self.temp = tempfile.TemporaryDirectory()
        self.original = database.DB_PATH
        database.DB_PATH = Path(self.temp.name) / 'test.db'
        database.init_db()
        self.pdfs = sorted((Path(__file__).resolve().parents[1] / 'invoices').glob('26952000004515245716*.pdf'))

    def tearDown(self):
        database.DB_PATH = self.original
        self.temp.cleanup()

    def test_mixed_import_and_repeat(self):
        broken = Path(self.temp.name) / 'broken.pdf'
        broken.write_bytes(b'not a pdf')
        with contextlib.redirect_stderr(io.StringIO()):
            result = import_invoices([broken, *self.pdfs])
        self.assertEqual((result['added'], result['duplicates'], len(result['errors'])), (1, 1, 1))
        self.assertEqual(import_invoices(self.pdfs)['duplicates'], 2)
        self.assertEqual(len(database.list_invoices()), 1)

    def test_import_module_has_no_database_side_effect(self):
        with patch('invoice_manager.database.init_db', side_effect=AssertionError('unexpected write')):
            from invoice_manager import read_invoice
            importlib.reload(read_invoice)

    def test_status_transaction_and_filters(self):
        import_invoices(self.pdfs)
        number = database.list_invoices()[0]['invoice_number']
        with self.assertRaises(ValueError):
            database.update_status_batch([number, 'missing'], 'used')
        self.assertEqual(database.list_invoices()[0]['status'], 'available')
        database.update_status_batch([number], 'reserved')
        database.update_printed_batch([number], True)
        database.update_remark(number, '差旅报销')
        rows = database.list_invoices()
        self.assertEqual(len(filter_invoices(rows, '差旅', '已预留', '已打印')), 1)
        self.assertEqual(filter_invoices(rows, '', '待使用'), [])
        with self.assertRaises(ValueError):
            database.update_status_batch([number], 'invalid')

    def test_gui_workflow(self):
        import tkinter as tk
        root = tk.Tk()
        self.addCleanup(root.destroy)
        app = InvoiceApp(root)
        root.update()
        self.assertEqual(str(app.delete_button['state']), 'disabled')
        import_invoices(self.pdfs)
        app.refresh()
        app.select_all()
        self.assertIn('428.00', app.selection_summary.get())
        app.mark_printed(True)
        self.assertEqual(database.list_invoices()[0]['is_printed'], 1)
        app.query.set('not found')
        self.assertEqual(len(app.table.get_children()), 0)
        self.assertEqual(str(app.delete_button['state']), 'disabled')
        app.reset_filters()
        app.select_all()
        with patch('invoice_manager.app.simpledialog.askstring', return_value='已核对'):
            app.edit_remark()
        self.assertEqual(database.list_invoices()[0]['remark'], '已核对')
        root.update()

    def test_reimbursement_lifecycle(self):
        import_invoices(self.pdfs)
        number = database.list_invoices()[0]['invoice_number']
        batch = database.create_reimbursement_batch('2026-10', 20, 2500, [number])
        self.assertEqual(database.list_invoices()[0]['status'], 'reserved')
        with self.assertRaises(ValueError):
            database.update_status_batch([number], 'available')
        with self.assertRaises(ValueError):
            database.create_reimbursement_batch('2026-10', 20, 2500, [number])
        self.assertEqual(len(database.list_reimbursement_batches()), 1)
        database.finish_reimbursement_batch(batch, 'cancelled')
        self.assertEqual(database.list_invoices()[0]['status'], 'available')
        batch = database.create_reimbursement_batch('2026-11', 20, 2500, [number])
        database.finish_reimbursement_batch(batch, 'used')
        self.assertEqual(database.list_invoices()[0]['status'], 'used')
        with self.assertRaises(ValueError):
            database.finish_reimbursement_batch(batch, 'cancelled')
        with self.assertRaises(ValueError):
            database.update_status_batch([number], 'available')

    def test_selector_exact_match_and_available_only(self):
        from invoice_manager.invoice_selector import select_invoice_combination
        rows = [dict(invoice_number=str(i), total_amount_fen=amount,
                     invoice_date='2026-10-01', status=status)
                for i, (amount, status) in enumerate([(42800, 'available'), (23200, 'available'),
                    (26800, 'available'), (50000, 'reserved')])]
        selected = select_invoice_combination(rows, 50000)
        self.assertEqual({row['invoice_number'] for row in selected}, {'1', '2'})
        self.assertEqual(sum(row['total_amount_fen'] for row in selected), 50000)
        self.assertEqual(select_invoice_combination([rows[-1]], 50000), [])

    def test_nearest_above_and_below(self):
        from invoice_manager.invoice_selector import select_invoice_combination
        def rows(amounts):
            return [dict(invoice_number=str(i), invoice_date='2026-10-01',
                         total_amount_fen=value, status='available') for i, value in enumerate(amounts)]
        self.assertEqual(sum(r['total_amount_fen'] for r in select_invoice_combination(rows([49000, 90000]), 50000)), 49000)
        self.assertEqual(sum(r['total_amount_fen'] for r in select_invoice_combination(rows([51000, 90000]), 50000)), 51000)

    def test_used_invoice_cannot_return_to_inventory(self):
        import_invoices(self.pdfs)
        number = database.list_invoices()[0]['invoice_number']
        database.update_status_batch([number], 'used')
        database.init_db()
        self.assertEqual(database.list_invoices()[0]['status'], 'used')
        with self.assertRaises(ValueError):
            database.update_status_batch([number], 'available')

    def test_reimbursement_inputs(self):
        self.assertEqual(reimbursement_inputs('2026-10', '20', '25.50'), ('2026-10', 20, 2550))
        for values in [('2026-1', '20', '25'), ('2026-13', '20', '25'),
                       ('2026-10', '1.5', '25'), ('2026-10', '32', '25'),
                       ('2026-10', '20', 'NaN'), ('2026-10', '20', '25.001')]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                reimbursement_inputs(*values)

    def make_app(self):
        import tkinter as tk
        root = tk.Tk()
        self.addCleanup(root.destroy)
        app = InvoiceApp(root)
        app.days.set('20')
        return root, app

    def test_generate_reserves_immediately_and_repeated_click_does_not_duplicate(self):
        import_invoices(self.pdfs)
        root, app = self.make_app()
        with patch('tkinter.messagebox.askyesno') as dialog:
            app.workflow.generate()
            app.workflow.generate()
            dialog.assert_not_called()
        self.assertEqual(len(database.list_reimbursement_batches()), 1)
        self.assertEqual(database.list_invoices()[0]['status'], 'reserved')
        root.update()
        self.assertFalse(app.workflow.form.winfo_ismapped())
        self.assertTrue(app.workflow.batch_view.finish_button.winfo_ismapped())
        app.show_batches()
        app.history.tree.selection_set('1')
        app.history.open_selected()
        self.assertEqual(app.notebook.select(), str(app.month_tab))

    def test_invalid_inputs_do_not_create_or_reserve(self):
        import_invoices(self.pdfs)
        _, app = self.make_app()
        app.days.set('invalid')
        app.workflow.generate()
        self.assertIsNone(app.workflow.batch_view)
        self.assertEqual(database.list_reimbursement_batches(), [])
        self.assertEqual(database.list_invoices()[0]['status'], 'available')

    def test_inventory_changed_before_commit_does_not_create_second_batch(self):
        import_invoices(self.pdfs)
        _, app = self.make_app()
        original_create = database.create_reimbursement_batch
        number = database.list_invoices()[0]['invoice_number']
        def concurrent_create(*args, **kwargs):
            original_create('2026-10', 20, 2500, [number])
            return original_create(*args, **kwargs)
        with patch('invoice_manager.database.create_reimbursement_batch', side_effect=concurrent_create):
            app.workflow.generate()
        self.assertIsNone(app.workflow.batch_view)
        self.assertEqual(len(database.list_reimbursement_batches()), 1)
        self.assertIn('未创建', app.workflow.feedback.get())

    def test_complete_resets_everything_and_preserves_history(self):
        from datetime import date
        import_invoices(self.pdfs)
        root, app = self.make_app()
        app.allowance.set('30')
        app.workflow.generate()
        view = app.workflow.batch_view
        with patch('tkinter.messagebox.askyesno', return_value=False):
            view.request_finish('used')
        self.assertIs(app.workflow.batch_view, view)
        self.assertEqual(database.list_invoices()[0]['status'], 'reserved')
        with patch('tkinter.messagebox.askyesno', return_value=True):
            view.request_finish('used')
        root.update()
        self.assertIsNone(app.workflow.batch_view)
        self.assertEqual(app.days.get(), '')
        self.assertEqual(app.month.get(), date.today().strftime('%Y-%m'))
        self.assertEqual(app.allowance.get(), '30')
        self.assertEqual(app.workflow.other_checked, set())
        self.assertFalse(app.workflow.other_frame.winfo_ismapped())
        self.assertTrue(app.workflow.form.winfo_ismapped())
        self.assertEqual(app.workflow.stage.get(), '本月报销')
        self.assertIn('记录已存入历史', app.workflow.feedback.get())
        self.assertEqual(database.list_invoices()[0]['status'], 'used')
        self.assertEqual(app.history.tree.item('1', 'values')[-1], '已报销')
        second_root, second_app = self.make_app()
        self.assertEqual(second_app.allowance.get(), '30')

    def test_failed_completion_keeps_current_view_and_inputs(self):
        import_invoices(self.pdfs)
        _, app = self.make_app()
        app.workflow.generate()
        view = app.workflow.batch_view
        with patch('tkinter.messagebox.askyesno', return_value=True), \
             patch('invoice_manager.database.finish_reimbursement_batch', side_effect=database.sqlite3.OperationalError('disk full')):
            view.request_finish('used')
        self.assertIs(app.workflow.batch_view, view)
        self.assertEqual(app.days.get(), '20')
        self.assertEqual(database.list_invoices()[0]['status'], 'reserved')
        self.assertIn('disk full', view.feedback.get())
        self.assertIsNone(view.pending_action)

    def test_cancel_removes_record_resets_and_allows_new_batch(self):
        import_invoices(self.pdfs)
        root, app = self.make_app()
        app.workflow.generate()
        view = app.workflow.batch_view
        database.update_printed_batch([view.rows[0]['invoice_number']], True)
        with patch('tkinter.messagebox.askyesno', return_value=True):
            view.request_finish('cancelled')
        root.update()
        self.assertIsNone(app.workflow.batch_view)
        self.assertEqual(app.history.tree.get_children(), ())
        self.assertEqual(database.list_reimbursement_batches(), [])
        self.assertEqual(database.reimbursement_invoices(view.batch_id), [])
        row = database.list_invoices()[0]
        self.assertEqual((row['status'], row['is_printed']), ('available', 1))
        self.assertEqual(app.days.get(), '')
        app.days.set('20')
        app.workflow.generate()
        self.assertIsNotNone(app.workflow.batch_view)

    def test_reopen_pending_after_restart_and_history_keeps_active_batch(self):
        import tkinter as tk
        import_invoices(self.pdfs)
        _, app = self.make_app()
        app.workflow.generate()
        batch_id = app.workflow.batch_view.batch_id
        _, restarted = self.make_app()
        restarted.open_batch(batch_id)
        self.assertEqual(restarted.workflow.batch_view.batch_id, batch_id)
        database.finish_reimbursement_batch(batch_id, 'used')
        # Create another invoice so a new pending batch exists while history is viewed.
        from invoice_manager.read_invoice import parse_invoice
        invoice = parse_invoice(self.pdfs[0])
        invoice['invoice_number'] = '26000000000000000002'
        database.save_invoice(invoice)
        _, fresh = self.make_app()
        fresh.workflow.generate()
        current = fresh.workflow.batch_view
        fresh.open_batch(batch_id)
        self.assertIs(fresh.workflow.batch_view, current)
        self.assertTrue(any(isinstance(widget, tk.Toplevel) for widget in fresh.root.winfo_children()))
        self.assertEqual(current.batch_status, 'reserved')

    def test_async_gui_import(self):
        import tkinter as tk
        root = tk.Tk()
        self.addCleanup(root.destroy)
        app = InvoiceApp(root)
        app.start_import(self.pdfs)
        self.assertTrue(app.importing)
        deadline = time.monotonic() + 5
        while app.importing and time.monotonic() < deadline:
            root.update()
            time.sleep(0.01)
        self.assertFalse(app.importing, 'Import did not finish')
        self.assertEqual(len(app.table.get_children()), 1)
        self.assertIn('新增 1 张', app.feedback.get())
        self.assertIn('重复跳过 1 张', app.feedback.get())

    def test_print_batch_rolls_back_missing_invoice(self):
        import_invoices(self.pdfs)
        number = database.list_invoices()[0]['invoice_number']
        with self.assertRaises(ValueError):
            database.update_printed_batch([number, 'missing'], True)
        self.assertEqual(database.list_invoices()[0]['is_printed'], 0)


if __name__ == '__main__':
    unittest.main()
