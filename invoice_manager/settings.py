"""Small persistent preferences beside the user's database, never in bundled resources."""
import json
import os
from pathlib import Path
import tempfile

from invoice_manager import database


def load_settings():
    try:
        value = json.loads((database.DB_PATH.parent / 'settings.json').read_text(encoding='utf-8'))
        if not isinstance(value, dict):
            return {}
        return {key: setting for key, setting in value.items()
                if key in ('export_root', 'daily_allowance') and isinstance(setting, str)}
    except (OSError, ValueError):
        return {}


def save_settings(**changes):
    values = load_settings()
    values.update(changes)
    directory = database.DB_PATH.parent
    directory.mkdir(parents=True, exist_ok=True)
    handle, filename = tempfile.mkstemp(dir=directory, suffix='.json')
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as stream:
            json.dump(values, stream, ensure_ascii=False, indent=2)
        os.replace(filename, directory / 'settings.json')
    finally:
        Path(filename).unlink(missing_ok=True)
