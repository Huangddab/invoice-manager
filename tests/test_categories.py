import gc
from decimal import Decimal
from pathlib import Path
import sqlite3
import tempfile
import time
import zipfile
import tkinter as tk
import unittest
from unittest.mock import patch

import database
from app import InvoiceApp, filter_invoices
from invoice_selector import select_invoice_combination
from read_invoice import import_invoices, parse_invoice


class CategoryTests(unittest.TestCase):
    def setUp(self):
        # Collect prior destroyed Tk roots on the UI thread before worker tests.
        gc.collect()
        self.temp = tempfile.TemporaryDirectory()
        self.previous = database.DB_PATH
        database.DB_PATH = Path(self.temp.name) / 'invoices.db'
        database.init_db()
        self.pdf = next((Path(__file__).resolve().parents[1] / 'invoices').glob('26952000004515245716*.pdf'))

    def tearDown(self):
        database.DB_PATH = self.previous
        self.temp.cleanup()

    def add(self, number, amount, category):
        invoice = parse_invoice(self.pdf)
        invoice.update(invoice_number=number, total_amount=Decimal(amount), category=category)
        database.save_invoice(invoice)

    def app(self):
        root = tk.Tk()
        self.addCleanup(root.destroy)
        return root, InvoiceApp(root)

    def test_migrate_legacy_schema_and_keep_data(self):
        with sqlite3.connect(database.DB_PATH) as conn:
            conn.execute('DROP TABLE reimbursement_items')
            conn.execute('DROP TABLE reimbursement_batches')
            conn.execute('DROP TABLE invoices')
            conn.execute('''CREATE TABLE invoices (invoice_number TEXT PRIMARY KEY,
                total_amount_fen INTEGER NOT NULL, invoice_date TEXT NOT NULL,
                buyer TEXT NOT NULL, seller TEXT NOT NULL, file_path TEXT NOT NULL,
                is_printed INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'available',
                remark TEXT NOT NULL DEFAULT '')''')
            conn.execute("INSERT INTO invoices VALUES ('legacy', 42800, '2026-10-01', 'buyer', 'seller', 'test.pdf', 1, 'available', 'keep')")
            conn.execute('''CREATE TABLE reimbursement_batches (id INTEGER PRIMARY KEY,
                reimbursement_month TEXT NOT NULL, work_days INTEGER NOT NULL,
                daily_allowance_fen INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'reserved',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
            conn.execute("INSERT INTO reimbursement_batches (id, reimbursement_month, work_days, daily_allowance_fen) VALUES (1, '2026-10', 20, 2500)")
        database.init_db()
        database.init_db()
        row = database.list_invoices()[0]
        self.assertEqual((row['category'], row['remark'], row['is_printed']), ('meal', 'keep', 1))
        self.assertEqual(database.list_reimbursement_batches()[0]['other_amount_fen'], 0)

    def test_duplicate_import_preserves_existing_category(self):
        self.assertEqual(import_invoices([self.pdf], 'other')['added'], 1)
        self.assertEqual(import_invoices([self.pdf], 'meal')['duplicates'], 1)
        self.assertEqual(database.list_invoices()[0]['category'], 'other')
        self.assertEqual(select_invoice_combination(database.list_invoices(), 42800), [])
        database.init_db()
        self.assertEqual(database.list_invoices()[0]['category'], 'other')

    def test_category_changes_are_atomic_and_protect_linked_invoices(self):
        self.add('meal', '50', 'meal')
        self.add('other', '80', 'other')
        batch = database.create_reimbursement_batch('2026-10', 2, 2500, ['meal'])
        with self.assertRaises(ValueError):
            database.update_category_batch(['other', 'meal'], 'meal')
        self.assertEqual(next(row for row in database.list_invoices() if row['invoice_number'] == 'other')['category'], 'other')
        database.finish_reimbursement_batch(batch, 'cancelled')
        database.update_category_batch(['meal'], 'other')
        self.assertEqual(len(filter_invoices(database.list_invoices(), category='其他')), 2)
        database.update_status_batch(['meal'], 'used')
        with self.assertRaises(ValueError):
            database.update_category_batch(['meal'], 'meal')

    def test_auto_selection_excludes_other_even_for_exact_amount(self):
        self.add('meal', '49', 'meal')
        self.add('other', '50', 'other')
        rows = select_invoice_combination(database.list_invoices(), 5000)
        self.assertEqual([row['invoice_number'] for row in rows], ['meal'])

    def test_combined_monthly_flow_and_export_target(self):
        self.add('meal', '49', 'meal')
        self.add('other', '80', 'other')
        self.add('unselected', '200', 'other')
        root, app = self.app()
        app.days.set('2')
        app.allowance.set('25')
        app.workflow.toggle_other_panel()
        app.workflow.toggle_other_number('other')
        app.workflow.generate()
        self.assertEqual({row['invoice_number'] for row in app.workflow.proposal[1]}, {'meal', 'other'})
        self.assertIn('差额 ¥-1.00', app.workflow.total.get())
        app.workflow.confirm_proposal()
        root.update()
        view = app.workflow.batch_view
        batch = database.list_reimbursement_batches()[0]
        self.assertEqual((batch['other_amount_fen'], batch['total_amount_fen']), (8000, 12900))
        self.assertEqual(app.history.tree.item(str(batch['id']), 'values')[3], '130.00')
        with patch('batch_view.filedialog.asksaveasfilename', return_value='') as dialog:
            view.export()
        self.assertEqual(dialog.call_args.kwargs['initialfile'], '2026-10应报销：130实际报销：129差额-1.zip')
        destination = str(Path(self.temp.name) / 'combined.zip')
        with patch('batch_view.filedialog.asksaveasfilename', return_value=destination):
            view.export()
        with zipfile.ZipFile(destination) as archive:
            self.assertEqual(len(archive.namelist()), 2)
        self.assertEqual(database.list_reimbursement_batches()[0]['status'], 'reserved')
        view.request_finish('used')
        view.confirm_pending()
        states = {row['invoice_number']: row['status'] for row in database.list_invoices()}
        self.assertEqual(states, {'meal': 'used', 'other': 'used', 'unselected': 'available'})

    def test_other_only_cancel_and_reuse(self):
        self.add('other', '80', 'other')
        _, app = self.app()
        app.days.set('0')
        app.workflow.toggle_other_number('other')
        app.workflow.generate()
        self.assertIsNotNone(app.workflow.proposal)
        app.workflow.confirm_proposal()
        with patch('batch_view.messagebox.askyesno', return_value=True):
            app.workflow.batch_view.request_finish('cancelled')
        self.assertEqual(database.list_reimbursement_batches(), [])
        self.assertEqual(database.list_invoices()[0]['status'], 'available')
        self.assertEqual(database.list_invoices()[0]['category'], 'other')
        self.assertIsNone(app.workflow.batch_view)

    def test_changed_category_rejects_stale_proposal(self):
        self.add('meal', '50', 'meal')
        _, app = self.app()
        app.days.set('2')
        app.workflow.generate()
        database.update_category_batch(['meal'], 'other')
        app.workflow.confirm_proposal()
        self.assertEqual(database.list_reimbursement_batches(), [])
        self.assertEqual(database.list_invoices()[0]['status'], 'available')
        self.assertIn('分类已变更', app.workflow.feedback.get())

    def test_stock_batch_classification(self):
        self.add('meal', '50', 'meal')
        root, app = self.app()
        app.notebook.select(app.stock_tab)
        root.update()
        app.table.selection_set('meal')
        app.move_category('other')
        app.category.set('其他')
        self.assertEqual(app.table.get_children(), ('meal',))
        self.assertEqual([row['invoice_number'] for row in app.workflow.other_rows], ['meal'])

    def test_ui_import_uses_selected_category(self):
        root, app = self.app()
        app.import_category.set('其他')
        app.start_import([self.pdf])
        deadline = time.monotonic() + 5
        while app.importing and time.monotonic() < deadline:
            root.update()
            time.sleep(0.01)
        self.assertFalse(app.importing)
        self.assertEqual(database.list_invoices()[0]['category'], 'other')
        self.assertEqual(len(app.workflow.other_rows), 1)
