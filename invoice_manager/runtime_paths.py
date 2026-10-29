"""Keep mutable user data outside PyInstaller's temporary extraction directory."""
import os
from pathlib import Path
import sys


def data_directory():
    override = os.environ.get('INVOICE_MANAGER_DATA_DIR')
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent / 'data'
    return Path(__file__).resolve().parents[1]


def resource_path(name):
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS) / name
    root = Path(__file__).resolve().parents[1]
    tool = root / 'tools' / name
    return tool if tool.is_file() else root / name
