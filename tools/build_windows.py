"""Build and verify a self-contained Windows executable without bundling user data."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def tkinter_data_args():
    """Explicitly bundle Tcl/Tk scripts even if the PyInstaller hook misses them."""
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    try:
        tcl = Path(root.tk.eval('info library'))
        toolkit = Path(root.tk.eval('set tk_library'))
    finally:
        root.destroy()
    if not (tcl / 'init.tcl').is_file() or not (toolkit / 'tk.tcl').is_file():
        raise RuntimeError('Python Tcl/Tk installation is incomplete. Repair Python with Tcl/Tk enabled.')
    args = ['--add-data', str(tcl) + ';_tcl_data', '--add-data', str(toolkit) + ';_tk_data']
    # Tcl packages, including msgcat, can be siblings of the core library.
    for name in ('tcl8', 'tcl9'):
        directory = tcl.parent / name
        if directory.is_dir():
            args.extend(['--add-data', str(directory) + ';' + name])
    return args


def verify_executable(executable):
    with tempfile.TemporaryDirectory(prefix='invoice-build-check-') as temporary:
        result = Path(temporary) / 'result.json'
        subprocess.run([str(executable), '--self-check', str(result)], check=True, timeout=60)
        if not result.is_file():
            raise RuntimeError('EXE did not produce its self-check result. Build was not accepted.')
        report = json.loads(result.read_text(encoding='utf-8'))
        if report.get('status') != 'passed' or Path(report['data_directory']).resolve() != executable.parent / 'data':
            raise RuntimeError('EXE self-check failed. Build was not accepted.')


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
    tk_args = tkinter_data_args()
    staging = root / 'build' / 'verified-output'
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
    destination = root / 'dist' / 'InvoiceManager.exe'
    destination.parent.mkdir(exist_ok=True)
    pending = destination.with_name('InvoiceManager.new.exe')
    try:
        shutil.copy2(executable, pending)
        pending.replace(destination)
    finally:
        pending.unlink(missing_ok=True)
    print('Verified portable executable:', destination)


if __name__ == '__main__':
    build()
