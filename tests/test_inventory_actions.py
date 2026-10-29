import gc
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

import database
from app import InvoiceApp
from read_invoice import import_invoices


class InventoryActionTests(unittest.TestCase):
    def setUp(self):
        gc.collect()
        self.temp = tempfile.TemporaryDirectory()
        self.old_db = database.DB_PATH
        database.DB_PATH = Path(self.temp.name) / 'invoices.db'
        database.init_db()
        self.pdf = next((Path(__file__).resolve().parents[1] / 'invoices').glob('26952000004515245716*.pdf'))
        import_invoices([self.pdf])
        self.row = database.list_invoices()[0]
        self.number = self.row['invoice_number']

    def tearDown(self):
        database.DB_PATH = self.old_db
        self.temp.cleanup()

    def app(self):
        root = tk.Tk()
        self.addCleanup(root.destroy)
        app = InvoiceApp(root)
        app.notebook.select(app.stock_tab)
        root.update()
        return root, app

    def test_double_click_remark_save_cancel_and_clear(self):
        root, app = self.app()
        self.assertFalse(hasattr(app, 'remark_entry'))
        x, y, width, height = app.table.bbox(self.number, 'seller')
        from types import SimpleNamespace
        event = SimpleNamespace(y=y + height // 2)
        with patch('app.simpledialog.askstring', return_value='  已核对  ') as dialog:
            app.edit_remark(event)
        self.assertEqual(database.list_invoices()[0]['remark'], '已核对')
        self.assertEqual(dialog.call_args.kwargs['initialvalue'], '')
        with patch('app.simpledialog.askstring', return_value=None):
            app.edit_remark()
        self.assertEqual(database.list_invoices()[0]['remark'], '已核对')
        with patch('app.simpledialog.askstring', return_value=''):
            app.edit_remark()
        self.assertEqual(database.list_invoices()[0]['remark'], '')
        with patch('app.simpledialog.askstring') as dialog:
            app.edit_remark(SimpleNamespace(y=app.table.winfo_height() + 10))
        dialog.assert_not_called()

    def test_delete_confirmation_and_keep_pdf(self):
        _, app = self.app()
        app.table.selection_set(self.number)
        with patch('app.messagebox.askyesno', return_value=False):
            app.delete_selected()
        self.assertEqual(len(database.list_invoices()), 1)
        with patch('app.messagebox.askyesno', return_value=True):
            app.delete_selected()
        self.assertEqual(database.list_invoices(), [])
        self.assertEqual(app.table.get_children(), ())
        self.assertTrue(Path(self.row['file_path']).is_file())
        self.assertEqual(import_invoices([self.pdf])['added'], 1)

    def test_linked_invoice_deletion_is_blocked(self):
        batch = database.create_reimbursement_batch('2026-10', 20, 2500, [self.number])
        with self.assertRaises(ValueError):
            database.delete_invoices([self.number])
        self.assertEqual(len(database.reimbursement_invoices(batch)), 1)
        database.finish_reimbursement_batch(batch, 'used')
        with self.assertRaises(ValueError):
            database.delete_invoices([self.number])
        self.assertEqual(len(database.list_reimbursement_batches()), 1)

    def test_batch_delete_rolls_back_on_missing_record(self):
        with self.assertRaises(ValueError):
            database.delete_invoices([self.number, 'missing'])
        self.assertEqual(len(database.list_invoices()), 1)
        database.update_status_batch([self.number], 'unusable')
        self.assertEqual(database.delete_invoices([self.number]), 1)
