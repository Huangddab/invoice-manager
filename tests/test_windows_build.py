import gc
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools.build_windows import build, materialize_library, publish_executable, tkinter_data_args, verify_executable


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
            window.tk.call.return_value = 0
            with patch('tkinter.Tk', return_value=window):
                args = tkinter_data_args(root / 'export')
            self.assertIn(str(tcl) + ';_tcl_data', args)
            self.assertIn(str(tk) + ';_tk_data', args)
            window.destroy.assert_called_once()
            (tk / 'tk.tcl').unlink()
            window.tk.eval.side_effect = [str(tcl), str(tk)]
            window.tk.call.return_value = 0
            with patch('tkinter.Tk', return_value=window), self.assertRaisesRegex(RuntimeError, 'not readable'):
                tkinter_data_args(root / 'export')

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

    def test_export_real_tcl_zipfs_library(self):
        import tkinter as tk
        import zipfile
        interpreter = tk.Tcl()
        if not interpreter.call('info', 'commands', 'zipfs'):
            self.skipTest('zipfs requires Tcl 9')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / 'libraries.zip'
            with zipfile.ZipFile(archive, 'w') as output:
                output.writestr('lib/tcl9.0/init.tcl', '# example init')
                output.writestr('lib/tcl9.0/encoding/test.enc', '# example encoding')
                output.writestr('lib/tk9.0/tk.tcl', '# example Tk')
            mount = '/invoice-build-test'
            interpreter.call('zipfs', 'mount', str(archive), mount)
            try:
                virtual = '//zipfs:/invoice-build-test/lib/tcl9.0'
                self.assertFalse(Path(virtual).is_dir())
                exported = materialize_library(interpreter, virtual, root / 'exported', 'init.tcl')
                self.assertEqual((exported / 'init.tcl').read_text(), '# example init')
                self.assertTrue((exported / 'encoding' / 'test.enc').is_file())
                tk_exported = materialize_library(interpreter, '//zipfs:/invoice-build-test/lib/tk9.0', root / 'tk-exported', 'tk.tcl')
                self.assertTrue((tk_exported / 'tk.tcl').is_file())
            finally:
                interpreter.call('zipfs', 'unmount', mount)

    def test_locked_old_exe_keeps_new_build_and_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'verified.exe'
            source.write_bytes(b'new verified build')
            destination = root / 'dist' / 'InvoiceManager.exe'
            destination.parent.mkdir()
            destination.write_bytes(b'old running build')
            data = destination.parent / 'data'
            data.mkdir()
            (data / 'invoices.db').write_bytes(b'user database')
            replace = Path.replace
            def locked(path, target):
                if target == destination:
                    raise PermissionError('EXE in use')
                return replace(path, target)
            with patch.object(Path, 'replace', locked):
                output = publish_executable(source, destination)
            self.assertNotEqual(output, destination)
            self.assertEqual(output.parent, destination.parent)
            self.assertEqual(output.read_bytes(), b'new verified build')
            self.assertEqual(destination.read_bytes(), b'old running build')
            self.assertEqual((data / 'invoices.db').read_bytes(), b'user database')
            self.assertEqual(list(destination.parent.glob('*.new.exe')), [])

    def test_unlocked_exe_is_replaced_normally(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'verified.exe'
            source.write_bytes(b'new verified build')
            destination = root / 'dist' / 'InvoiceManager.exe'
            self.assertEqual(publish_executable(source, destination), destination)
            self.assertEqual(destination.read_bytes(), b'new verified build')
