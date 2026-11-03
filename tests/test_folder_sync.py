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
from invoice_manager.folder_sync import FolderSync, fingerprint, scan_directories
from invoice_manager.settings import load_settings, save_settings


TEXT = '''深圳电子普通发票
发票代码：100000000001
发票号码：00000001
开票日期：2026 年 11 月 03 日
   名    称：示例购买公司      密    123456
价税合计（大写） 玖佰元整 （小写）¥900.00
   名    称：示例餐饮公司
'''


def make_pdf(path, serial='00000001'):
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((20, 25), TEXT.replace('00000001', serial), fontname='china-s', fontsize=10)
        document.save(path)


class FolderSyncTests(unittest.TestCase):
    def setUp(self):
        gc.collect()
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.folder = self.directory / 'source'
        self.folder.mkdir()
        self.old_db = database.DB_PATH
        database.DB_PATH = self.directory / 'invoices.db'
        database.init_db()

    def tearDown(self):
        database.DB_PATH = self.old_db
        self.temp.cleanup()

    def make_app(self):
        root = tk.Tk()
        self.addCleanup(root.destroy)
        return root, InvoiceApp(root)

    def tick(self, root, app):
        app.check_sync()
        deadline = time.monotonic() + 5
        while (app.sync_scanning or app.importing) and time.monotonic() < deadline:
            root.update()
            time.sleep(0.01)
        self.assertFalse(app.sync_scanning or app.importing)
        root.update()

    def test_stable_files_and_durable_incremental_state(self):
        source = self.folder / 'new.PDF'
        source.write_bytes(b'partial')
        folders = {'meal': str(self.folder)}
        sync = FolderSync()
        first, errors = scan_directories(folders)
        self.assertFalse(errors)
        self.assertEqual(sync.ready(first), {})
        source.write_bytes(b'complete file with different size')
        second, _ = scan_directories(folders)
        self.assertEqual(sync.ready(second), {})
        ready = sync.ready(second)
        self.assertEqual(list(ready), [str(source)])
        sync.remember(ready)
        self.assertEqual(sync.ready(second), {})
        restarted = FolderSync()
        self.assertEqual(restarted.ready(second), {})
        self.assertEqual(restarted.ready(second), {})
        source.unlink()
        self.assertEqual(restarted.ready(scan_directories(folders)[0]), {})

    def test_empty_scan_remembered_and_new_files_import_without_changing_filters(self):
        root, app = self.make_app()
        with patch('invoice_manager.app.filedialog.askdirectory', return_value=str(self.folder)):
            app.scan_folder('other')
        self.assertFalse(app.importing)
        self.assertEqual(load_settings()['scan_folders'], {'other': str(self.folder)})
        make_pdf(self.folder / 'new.pdf')
        app.query.set('unmatched query')
        self.tick(root, app)
        self.assertEqual(database.list_invoices(), [])
        self.tick(root, app)
        self.assertEqual(database.list_invoices()[0]['category'], 'other')
        self.assertEqual(app.query.get(), 'unmatched query')
        self.assertEqual(app.category.get(), '全部')
        self.assertEqual(app.table.get_children(), ())
        last_result = app.last_import_result
        self.tick(root, app)
        self.assertIs(app.last_import_result, last_result)
        # Deleting the source is never a request to delete inventory.
        (self.folder / 'new.pdf').unlink()
        self.tick(root, app)
        self.assertEqual(len(database.list_invoices()), 1)

    def test_restart_catches_new_files_but_does_not_restore_deleted_inventory(self):
        save_settings(scan_folders={'meal': str(self.folder)})
        source = self.folder / 'first.pdf'
        make_pdf(source)
        root, app = self.make_app()
        self.tick(root, app)
        self.tick(root, app)
        number = database.list_invoices()[0]['invoice_number']
        database.delete_invoices([number])
        make_pdf(self.folder / 'second.pdf', '00000002')
        restarted_root, restarted = self.make_app()
        self.tick(restarted_root, restarted)
        self.tick(restarted_root, restarted)
        rows = database.list_invoices()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['invoice_serial'], '00000002')

    def test_pause_resume_and_failure_only_once_until_file_changes(self):
        save_settings(scan_folders={'meal': str(self.folder)}, auto_sync=False)
        source = self.folder / 'broken.pdf'
        source.write_bytes(b'broken')
        root, app = self.make_app()
        self.tick(root, app)
        self.assertIsNone(app.last_import_result)
        app.auto_sync.set(True)
        app.toggle_sync()
        self.assertTrue(load_settings()['auto_sync'])
        self.tick(root, app)
        self.tick(root, app)
        self.assertEqual(len(app.last_import_result['errors']), 1)
        self.assertTrue(app.import_result_window.winfo_exists())
        last_result = app.last_import_result
        self.tick(root, app)
        self.tick(root, app)
        self.assertIs(app.last_import_result, last_result)
        source.unlink()
        make_pdf(source)
        self.tick(root, app)
        self.tick(root, app)
        self.assertEqual(len(database.list_invoices()), 1)
        self.assertEqual(app.last_import_result['errors'], [])

    def test_unavailable_directory_reports_problem_without_deleting_inventory(self):
        missing = self.directory / 'missing'
        files, errors = scan_directories({'meal': str(missing)})
        self.assertEqual(files, {})
        self.assertIn(str(missing), errors)
        save_settings(scan_folders={'meal': str(missing)})
        root, app = self.make_app()
        self.tick(root, app)
        self.assertIn('暂不可用', app.sync_status.get())
        self.assertFalse(app.importing)

    def test_file_changed_while_queued_waits_for_stability_again(self):
        save_settings(scan_folders={'meal': str(self.folder)})
        source = self.folder / 'queued.pdf'
        source.write_bytes(b'partial')
        token = fingerprint(source, 'meal')
        root, app = self.make_app()
        app.sync_pending = [('meal', {str(source): token})]
        source.unlink()
        make_pdf(source)
        app.import_next_sync()
        self.tick(root, app)
        self.assertEqual(database.list_invoices(), [])
        self.assertNotIn(str(source), app.folder_sync.attempted)
        self.tick(root, app)
        self.tick(root, app)
        self.assertEqual(len(database.list_invoices()), 1)

    def test_preferences_keep_watch_configuration_when_export_setting_changes(self):
        save_settings(scan_folders={'meal': str(self.folder)}, auto_sync=False)
        save_settings(export_root=str(self.directory), daily_allowance='30')
        settings = load_settings()
        self.assertEqual(settings['scan_folders'], {'meal': str(self.folder)})
        self.assertFalse(settings['auto_sync'])
        self.assertEqual(settings['daily_allowance'], '30')
