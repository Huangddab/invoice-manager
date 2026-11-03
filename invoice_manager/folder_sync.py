"""Incremental folder discovery with stable-file checks and durable attempt signatures."""
import json
import os
from pathlib import Path
import tempfile

from invoice_manager import database


def fingerprint(path, category):
    stat = Path(path).stat()
    return category, stat.st_size, stat.st_mtime_ns


def scan_directories(folders):
    """Run off the UI thread: removable or network folders can be slow/unavailable."""
    files, errors = {}, {}
    for category, folder in folders.items():
        try:
            for path in sorted(Path(folder).iterdir()):
                if path.is_file() and path.suffix.lower() == '.pdf':
                    files[str(path.resolve())] = fingerprint(path, category)
        except OSError as error:
            errors[folder] = str(error)
    return files, errors


class FolderSync:
    def __init__(self):
        self.observed = {}
        self.attempted = {}
        self.state_file = database.DB_PATH.parent / 'folder-sync.json'
        try:
            values = json.loads(self.state_file.read_text(encoding='utf-8'))
            if isinstance(values, dict):
                self.attempted = {path: tuple(token) for path, token in values.items()
                                  if isinstance(token, list) and len(token) == 3
                                  and token[0] in ('meal', 'other')
                                  and type(token[1]) is int and type(token[2]) is int}
        except (OSError, ValueError):
            pass

    def ready(self, files):
        """Require identical signatures on two consecutive scans before importing."""
        ready = {path: token for path, token in files.items()
                 if self.observed.get(path) == token and self.attempted.get(path) != token}
        self.observed = dict(files)
        return ready

    def remember(self, signatures):
        self.attempted.update(signatures)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=self.state_file.parent, suffix='.json')
        try:
            with os.fdopen(handle, 'w', encoding='utf-8') as stream:
                json.dump(self.attempted, stream, ensure_ascii=False)
            os.replace(temporary, self.state_file)
        finally:
            Path(temporary).unlink(missing_ok=True)
