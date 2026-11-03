"""Managed originals, company export names, and independent print copies."""
from decimal import Decimal
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile


def original_path(invoice):
    path = Path(invoice['file_path'])
    if not path.is_file():
        # Older databases may contain absolute paths from another computer.
        from invoice_manager import database
        candidates = [database.DB_PATH.parent / 'managed_pdfs' / f"{invoice['invoice_number']}.pdf",
                      database.DB_PATH.parent / 'invoices' / path.name,
                      database.DB_PATH.parent / 'invoices' / str(path).replace('\\', '/').split('/')[-1]]
        path = next((candidate for candidate in candidates if candidate.is_file()), path)
    if not path.is_file():
        raise FileNotFoundError(f"找不到原 PDF：{invoice['invoice_number']}，请重新导入原文件")
    return path


def display_invoice_number(invoice):
    serial = invoice.get('invoice_serial') or invoice['invoice_number']
    code = invoice.get('invoice_code')
    return f'{serial}（代码{code}）' if code else serial


def export_name(invoice):
    seller = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', invoice['seller']).strip().rstrip('. ')
    seller = seller or '未知销售方'
    amount = format(Decimal(invoice['total_amount_fen']) / 100, '.2f').rstrip('0').rstrip('.')
    # Strip zeroes only after the decimal point (428.00 -> 428, 400.00 -> 400).
    number = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', invoice.get('invoice_serial') or invoice['invoice_number'])
    return f'{number}_{seller}_{amount}.pdf'


def batch_export_name(month, target_fen, invoices):
    def compact(fen):
        return format(Decimal(fen) / 100, '.2f').rstrip('0').rstrip('.')
    total = sum(row['total_amount_fen'] for row in invoices)
    return f'{month}应报销：{compact(target_fen)}实际报销：{compact(total)}差额{compact(total-target_fen)}.zip'


def export_zip(invoices, destination):
    invoices = list(invoices)
    if not invoices:
        raise ValueError('请至少勾选一张发票')
    files = [(original_path(row), export_name(row)) for row in invoices]
    if len({name for _, name in files}) != len(files):
        raise ValueError('导出文件名重复，请核对发票号码')
    destination = Path(destination)
    if any(destination.resolve() == source.resolve() for source, _ in files):
        raise ValueError('导出位置不能覆盖原始 PDF，请选择 ZIP 文件名')
    handle, temporary = tempfile.mkstemp(dir=destination.parent, suffix='.zip')
    os.close(handle)
    try:
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
            for source, name in files:
                archive.write(source, name)
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return len(files)


def prepare_print_pdf(invoices, destination):
    """A5 landscape, vector-preserving, one invoice per sheet, 5 mm margins."""
    try:
        import fitz
    except ImportError as error:
        raise RuntimeError('缺少打印所需的 PyMuPDF，请在项目目录执行：python -m pip install -r requirements.txt') from error
    rows = list(invoices)
    if not rows:
        raise ValueError('请至少勾选一张发票')
    width, height = 210 / 25.4 * 72, 148 / 25.4 * 72
    margin = 5 / 25.4 * 72
    with fitz.open() as output:
        for row in rows:
            with fitz.open(original_path(row)) as source:
                if len(source) != 1:
                    raise ValueError(f"发票 {row['invoice_number']} 有 {len(source)} 页，请人工核对；未发送打印")
                # Flatten stamps / annotations as PDF content, without rasterizing.
                source.bake()
                page = source[0]
                target = output.new_page(width=width, height=height)
                if not page.get_contents():
                    continue
                target.show_pdf_page(fitz.Rect(margin, margin, width - margin, height - margin), source, 0,
                                     keep_proportion=True)
        output.save(destination, deflate=True)


def edge_executable():
    executable = shutil.which('msedge.exe')
    if executable:
        return executable
    for variable in ('PROGRAMFILES(X86)', 'PROGRAMFILES', 'LOCALAPPDATA'):
        root = os.environ.get(variable)
        if root:
            candidate = Path(root) / 'Microsoft' / 'Edge' / 'Application' / 'msedge.exe'
            if candidate.is_file():
                return str(candidate)
    raise FileNotFoundError('未找到 Microsoft Edge，请安装 Edge 后重试')


def print_command(pdf):
    if sys.platform == 'win32':
        return [edge_executable(), '--new-window', Path(pdf).resolve().as_uri()]
    executable = shutil.which('lp')
    if not executable:
        raise FileNotFoundError('未找到系统打印命令 lp，请配置打印机后重试')
    return [executable, '-o', 'media=A5', '-o', 'orientation-requested=4', '-o', 'print-color-mode=monochrome',
            '-o', 'fit-to-page', '-o', 'number-up=1', str(pdf)]


def submit_print(command):
    if sys.platform == 'win32':
        # Edge can reuse an existing browser process; do not wait for it to close.
        subprocess.Popen(command)
        return
    result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace')
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or '打印命令未成功执行')
    # Opening a viewer / submitting a job never means physical print success.
