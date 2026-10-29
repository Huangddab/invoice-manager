"""Build and verify a self-contained Windows executable without bundling user data."""
from datetime import datetime
import json
import uuid
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def materialize_library(interpreter, source, destination, bootstrap=None):
    """Export Tcl virtual libraries (for example zipfs) to ordinary build files."""
    native = Path(source)
    try:
        if native.is_dir() and (bootstrap is None or (native / bootstrap).is_file()):
            return native
    except OSError:
        # Windows may reject zipfs paths as invalid native filenames.
        pass
    probe = interpreter.call('file', 'join', source, bootstrap) if bootstrap else source
    if not int(interpreter.call('file', 'readable', probe)):
        raise RuntimeError(f'Tcl/Tk library is not readable: {probe}. Python: {sys.executable}')
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    interpreter.call('file', 'copy', '-force', source, str(destination))
    if not destination.is_dir() or (bootstrap and not (destination / bootstrap).is_file()):
        raise RuntimeError(f'Tcl/Tk library export failed: {source} -> {destination}')
    return destination


def tkinter_data_args(export_root):
    """Use Tcl file operations so both native paths and embedded zipfs work."""
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    try:
        tcl_source = root.tk.eval('info library')
        tk_source = root.tk.eval('set tk_library')
        print('Tcl library:', tcl_source)
        print('Tk library:', tk_source)
        tcl = materialize_library(root.tk, tcl_source, Path(export_root) / 'tcl', 'init.tcl')
        toolkit = materialize_library(root.tk, tk_source, Path(export_root) / 'tk', 'tk.tcl')
        args = ['--add-data', str(tcl) + ';_tcl_data', '--add-data', str(toolkit) + ';_tk_data']
        parent = root.tk.call('file', 'dirname', tcl_source)
        for name in ('tcl8', 'tcl9'):
            source = root.tk.call('file', 'join', parent, name)
            if int(root.tk.call('file', 'isdirectory', source)):
                directory = materialize_library(root.tk, str(source), Path(export_root) / name)
                args.extend(['--add-data', str(directory) + ';' + name])
        return args
    finally:
        root.destroy()


def verify_executable(executable):
    with tempfile.TemporaryDirectory(prefix='invoice-build-check-') as temporary:
        result = Path(temporary) / 'result.json'
        subprocess.run([str(executable), '--self-check', str(result)], check=True, timeout=60)
        if not result.is_file():
            raise RuntimeError('EXE did not produce its self-check result. Build was not accepted.')
        report = json.loads(result.read_text(encoding='utf-8'))
        if report.get('status') != 'passed' or Path(report['data_directory']).resolve() != executable.parent / 'data':
            raise RuntimeError('EXE self-check failed. Build was not accepted.')


def publish_executable(executable, destination):
    """Keep verified builds even when Windows has locked the previous EXE."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    pending = destination.with_name(f'.InvoiceManager-{uuid.uuid4().hex}.new.exe')
    try:
        shutil.copy2(executable, pending)
        try:
            pending.replace(destination)
            return destination
        except PermissionError:
            alternative = destination.with_name(f'InvoiceManager-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}.exe')
            pending.replace(alternative)
            print('The old EXE could not be replaced; it may still be running.')
            print('Close the old application before starting this new EXE. Existing data is unchanged.')
            return alternative
    finally:
        pending.unlink(missing_ok=True)


def build():
    if sys.platform != 'win32':
        raise SystemExit('Windows EXE must be built on Windows. Use the Windows GitHub Actions workflow.')
    root = Path(__file__).resolve().parents[1]
    if '--in-build-env' not in sys.argv:
        environment = root / '.build-venv'
        python = environment / 'Scripts' / 'python.exe'
        if not python.is_file():
            subprocess.run([sys.executable, '-m', 'venv', str(environment)], check=True)
        subprocess.run([str(python), str(Path(__file__).resolve()), '--in-build-env'], check=True)
        return
    subprocess.run([sys.executable, '-m', 'pip', 'install', '-r', str(root / 'requirements-build.txt')], check=True)
    staging = root / 'build' / 'verified-output'
    with tempfile.TemporaryDirectory(prefix='invoice-tcl-build-') as libraries:
        tk_args = tkinter_data_args(Path(libraries))
        subprocess.run([
            sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile', '--windowed',
            '--name', 'InvoiceManager', '--paths', str(root), '--collect-all', 'pymupdf',
            '--hidden-import', 'fitz', '--add-data', str(root / 'tools' / 'windows_print.ps1') + ';.',
            *tk_args,
            '--distpath', str(staging), '--workpath', str(root / 'build' / 'pyinstaller'),
            '--specpath', str(root / 'build'), str(root / 'tools' / 'desktop_entry.py'),
        ], cwd=root, check=True)
    executable = staging / 'InvoiceManager.exe'
    verify_executable(executable)
    destination = publish_executable(executable, root / 'dist' / 'InvoiceManager.exe')
    print('Verified portable executable:', destination)


if __name__ == '__main__':
    build()
