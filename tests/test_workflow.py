import gc
import contextlib
import importlib
import io
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import database
from app import InvoiceApp, filter_invoices, reimbursement_inputs
from read_invoice import import_invoices


class WorkflowTests(unittest.TestCase):
    def setUp(self):
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
        with patch('database.init_db', side_effect=AssertionError('unexpected write')):
            import read_invoice
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
        with patch('app.simpledialog.askstring', return_value='已核对'):
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
        from invoice_selector import select_invoice_combination
        rows = [dict(invoice_number=str(i), total_amount_fen=amount,
                     invoice_date='2026-10-01', status=status)
                for i, (amount, status) in enumerate([(42800, 'available'), (23200, 'available'),
                    (26800, 'available'), (50000, 'reserved')])]
        selected = select_invoice_combination(rows, 50000)
        self.assertEqual({row['invoice_number'] for row in selected}, {'1', '2'})
        self.assertEqual(sum(row['total_amount_fen'] for row in selected), 50000)
        self.assertEqual(select_invoice_combination([rows[-1]], 50000), [])

    def test_nearest_above_and_below(self):
        from invoice_selector import select_invoice_combination
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

    def test_gui_recommend_and_create_batch(self):
        import tkinter as tk
        import_invoices(self.pdfs)
        root = tk.Tk()
        self.addCleanup(root.destroy)
        app = InvoiceApp(root)
        app.query.set('no results')
        app.recommend()
        self.assertIsNotNone(app.workflow.proposal)
        self.assertEqual(database.list_invoices()[0]['status'], 'available')
        self.assertEqual(database.list_reimbursement_batches(), [])
        app.table.selection_remove(*app.table.selection())
        app.create_batch()
        self.assertEqual(len(database.list_reimbursement_batches()), 1)
        self.assertEqual(database.list_invoices()[0]['status'], 'reserved')
        self.assertIsNotNone(app.workflow.batch_view)
        app.show_batches()
        self.assertEqual(app.notebook.select(), str(app.history_tab))
        app.history.tree.selection_set('1')
        app.history.open_selected()
        self.assertEqual(app.notebook.select(), str(app.month_tab))
        root.update()
        self.assertFalse(any(isinstance(widget, tk.Toplevel) for widget in root.winfo_children()))
        view = app.workflow.batch_view
        self.assertTrue(view.finish_button.winfo_ismapped())
        self.assertGreater(view.tree.winfo_height(), 100)
        view.request_finish('used')
        root.update()
        self.assertTrue(view.confirm_button.winfo_ismapped())
        self.assertLess(view.confirm_button.winfo_rooty() + view.confirm_button.winfo_height(), root.winfo_rooty() + root.winfo_height())
        view.dismiss_pending()

    def test_changed_target_invalidates_unconfirmed_proposal(self):
        import tkinter as tk
        import_invoices(self.pdfs)
        root = tk.Tk()
        self.addCleanup(root.destroy)
        app = InvoiceApp(root)
        app.workflow.generate()
        self.assertIsNotNone(app.workflow.proposal)
        app.days.set('10')
        self.assertIsNone(app.workflow.proposal)
        app.workflow.confirm_proposal()
        self.assertEqual(database.list_reimbursement_batches(), [])
        self.assertEqual(database.list_invoices()[0]['status'], 'available')

    def test_reserved_elsewhere_rejects_stale_proposal(self):
        import tkinter as tk
        import_invoices(self.pdfs)
        root = tk.Tk()
        self.addCleanup(root.destroy)
        app = InvoiceApp(root)
        app.workflow.generate()
        number = database.list_invoices()[0]['invoice_number']
        database.create_reimbursement_batch('2026-10', 20, 2500, [number])
        app.workflow.confirm_proposal()
        self.assertIn('未创建', app.workflow.feedback.get())
        self.assertEqual(len(database.list_reimbursement_batches()), 1)

    def test_whole_reimbursement_flow_in_one_window(self):
        import tkinter as tk
        import_invoices(self.pdfs)
        root = tk.Tk()
        self.addCleanup(root.destroy)
        app = InvoiceApp(root)
        app.days.set('20')
        app.allowance.set('25')
        app.workflow.generate()
        app.workflow.confirm_proposal()
        view = app.workflow.batch_view
        destination = str(Path(self.temp.name) / 'export.zip')
        with patch('batch_view.filedialog.asksaveasfilename', return_value=destination):
            view.export()
        self.assertTrue(Path(destination).is_file())
        self.assertEqual(database.list_invoices()[0]['status'], 'reserved')
        with patch('batch_view.print_command', return_value=['mock']), patch('batch_view.submit_print'):
            view.print_checked()
            deadline = time.monotonic() + 5
            while view.busy and time.monotonic() < deadline:
                root.update()
                time.sleep(0.01)
        self.assertFalse(view.busy)
        self.assertEqual(database.list_invoices()[0]['is_printed'], 0)
        view.confirm_pending()
        self.assertEqual(database.list_invoices()[0]['is_printed'], 1)
        self.assertEqual(database.list_invoices()[0]['status'], 'reserved')
        view.request_finish('used')
        self.assertEqual(database.list_invoices()[0]['status'], 'reserved')
        view.confirm_pending()
        self.assertEqual(database.list_invoices()[0]['status'], 'used')
        self.assertIn('本单已完成报销', app.workflow.stage.get())
        self.assertEqual(app.history.tree.item('1', 'values')[-1], '已报销')
        self.assertFalse(any(isinstance(widget, tk.Toplevel) for widget in root.winfo_children()))

    def test_cancel_removes_record_and_clears_current_page(self):
        import tkinter as tk
        import_invoices(self.pdfs)
        root = tk.Tk()
        self.addCleanup(root.destroy)
        app = InvoiceApp(root)
        app.workflow.generate()
        app.workflow.confirm_proposal()
        view = app.workflow.batch_view
        database.update_printed_batch([view.rows[0]['invoice_number']], True)
        with patch('batch_view.messagebox.askyesno', return_value=True):
            view.request_finish('cancelled')
        root.update()
        self.assertIsNone(app.workflow.batch_view)
        self.assertEqual(app.workflow.preview.get_children(), ())
        self.assertEqual(app.history.tree.get_children(), ())
        self.assertEqual(database.list_reimbursement_batches(), [])
        self.assertEqual(database.reimbursement_invoices(view.batch_id), [])
        row = database.list_invoices()[0]
        self.assertEqual(row['status'], 'available')
        self.assertEqual(row['is_printed'], 1)
        app.workflow.generate()
        self.assertIsNotNone(app.workflow.proposal)

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
