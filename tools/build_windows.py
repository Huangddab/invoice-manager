"""Build one portable executable on Windows; never bundle user invoices."""
from pathlib import Path
import subprocess
import sys


def build():
    if sys.platform != 'win32':
        raise SystemExit('Windows EXE must be built on Windows. Use the Windows GitHub Actions workflow.')
    root = Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable, '-m', 'pip', 'install', '-r', str(root / 'requirements-build.txt')], check=True)
    subprocess.run([
        sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile', '--windowed',
        '--name', 'InvoiceManager', '--paths', str(root), '--collect-all', 'pymupdf',
        '--hidden-import', 'fitz', '--add-data', str(root / 'windows_print.ps1') + ';.',
        '--distpath', str(root / 'dist'), '--workpath', str(root / 'build'),
        '--specpath', str(root / 'build'), str(root / 'tools' / 'desktop_entry.py'),
    ], cwd=root, check=True)
    print(root / 'dist' / 'InvoiceManager.exe')


if __name__ == '__main__':
    build()
