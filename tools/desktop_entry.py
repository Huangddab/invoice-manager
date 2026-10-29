"""Frozen Windows entry point, including an isolated packaging smoke check."""
import json
from pathlib import Path
import sys
import tempfile

# Also allow running this entry point from the source checkout.
if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def self_check(destination):
    import tkinter as tk
    import fitz
    import pypdf
    from invoice_manager import database
    from invoice_manager.invoice_files import prepare_print_pdf
    from invoice_manager.runtime_paths import data_directory, resource_path
    assert resource_path('windows_print.ps1').is_file()
    assert pypdf.__version__
    with tempfile.TemporaryDirectory() as directory:
        temporary = Path(directory)
        database.DB_PATH = temporary / 'invoices.db'
        database.init_db()
        assert database.list_invoices() == []
        source = temporary / 'source.pdf'
        with fitz.open() as document:
            document.new_page()
            document.save(source)
        output = temporary / 'print.pdf'
        prepare_print_pdf([{'file_path': str(source), 'invoice_number': 'self-check'}], output)
        with fitz.open(output) as document:
            assert len(document) == 1 and document[0].rect.width > document[0].rect.height
        root = tk.Tk()
        root.withdraw()
        root.update()
        root.destroy()
    Path(destination).write_text(json.dumps({'status': 'passed', 'checks': ['sqlite', 'tkinter', 'pypdf', 'pymupdf', 'A5 print copy', 'print resource'], 'data_directory': str(data_directory())}), encoding='utf-8')


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--self-check':
        self_check(sys.argv[2])
    else:
        from invoice_manager.app import main
        main()
