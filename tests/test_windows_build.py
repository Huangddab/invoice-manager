import gc
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools.build_windows import build, tkinter_data_args, verify_executable


class WindowsBuildTests(unittest.TestCase):
    def setUp(self):
        gc.collect()

    def test_explicit_tcl_tk_files_match_runtime_hook_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tcl, tk = root / 'tcl9.0', root / 'tk9.0'
            tcl.mkdir()
            tk.mkdir()
            (tcl / 'init.tcl').write_text('# test')
            (tk / 'tk.tcl').write_text('# test')
            window = Mock()
            window.tk.eval.side_effect = [str(tcl), str(tk)]
            with patch('tkinter.Tk', return_value=window):
                args = tkinter_data_args()
            self.assertIn(str(tcl) + ';_tcl_data', args)
            self.assertIn(str(tk) + ';_tk_data', args)
            window.destroy.assert_called_once()
            (tk / 'tk.tcl').unlink()
            window.tk.eval.side_effect = [str(tcl), str(tk)]
            with patch('tkinter.Tk', return_value=window), self.assertRaisesRegex(RuntimeError, 'incomplete'):
                tkinter_data_args()

    def test_failed_exe_check_preserves_previous_executable_and_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'dist' / 'data').mkdir(parents=True)
            destination = root / 'dist' / 'InvoiceManager.exe'
            destination.write_bytes(b'previous working executable')
            database = root / 'dist' / 'data' / 'invoices.db'
            database.write_bytes(b'personal data')
            with patch('tools.build_windows.__file__', str(root / 'tools' / 'build_windows.py')), \
                 patch('tools.build_windows.sys.platform', 'win32'), \
                 patch('tools.build_windows.sys.argv', ['build_windows.py', '--in-build-env']), \
                 patch('tools.build_windows.subprocess.run'), \
                 patch('tools.build_windows.tkinter_data_args', return_value=[]), \
                 patch('tools.build_windows.verify_executable', side_effect=RuntimeError('failed')):
                with self.assertRaises(RuntimeError):
                    build()
            self.assertEqual(destination.read_bytes(), b'previous working executable')
            self.assertEqual(database.read_bytes(), b'personal data')

    def test_verification_requires_fresh_result(self):
        executable = Path('/tmp/staged/InvoiceManager.exe').resolve()
        with patch('tools.build_windows.subprocess.run'), self.assertRaisesRegex(RuntimeError, 'did not produce'):
            verify_executable(executable)
        def run(command, **kwargs):
            Path(command[-1]).write_text(json.dumps({'status': 'passed', 'data_directory': str(executable.parent / 'data')}), encoding='utf-8')
        with patch('tools.build_windows.subprocess.run', side_effect=run):
            verify_executable(executable)
