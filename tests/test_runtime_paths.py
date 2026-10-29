from pathlib import Path
import unittest
from unittest.mock import patch

from invoice_manager import runtime_paths


class RuntimePathTests(unittest.TestCase):
    def test_frozen_data_survives_temporary_extraction_directory(self):
        with patch.dict('os.environ', {'INVOICE_MANAGER_DATA_DIR': ''}), \
             patch('invoice_manager.runtime_paths.sys.frozen', True, create=True), \
             patch('invoice_manager.runtime_paths.sys.executable', '/tmp/portable/InvoiceManager.exe'), \
             patch('invoice_manager.runtime_paths.sys._MEIPASS', '/tmp/extracted', create=True):
            self.assertEqual(runtime_paths.data_directory(), Path('/tmp/portable/data').resolve())
            self.assertEqual(runtime_paths.resource_path('windows_print.ps1'), Path('/tmp/extracted/windows_print.ps1'))

    def test_source_data_directory_and_override(self):
        with patch.dict('os.environ', {'INVOICE_MANAGER_DATA_DIR': ''}), patch('invoice_manager.runtime_paths.sys.frozen', False, create=True):
            self.assertEqual(runtime_paths.data_directory(), Path(runtime_paths.__file__).resolve().parents[1])
        with patch.dict('os.environ', {'INVOICE_MANAGER_DATA_DIR': '/tmp/custom-data'}):
            self.assertEqual(runtime_paths.data_directory(), Path('/tmp/custom-data').resolve())
