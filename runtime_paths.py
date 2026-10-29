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
    return Path(__file__).resolve().parent


def resource_path(name):
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / name
