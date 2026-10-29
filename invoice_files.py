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
        import database
        candidates = [database.DB_PATH.parent / 'managed_pdfs' / f"{invoice['invoice_number']}.pdf",
                      database.DB_PATH.parent / 'invoices' / path.name,
                      database.DB_PATH.parent / 'invoices' / str(path).replace('\\', '/').split('/')[-1]]
        path = next((candidate for candidate in candidates if candidate.is_file()), path)
    if not path.is_file():
        raise FileNotFoundError(f"找不到原 PDF：{invoice['invoice_number']}，请重新导入原文件")
    return path


def export_name(invoice):
    seller = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', invoice['seller']).strip().rstrip('. ')
    seller = seller or '未知销售方'
    amount = format(Decimal(invoice['total_amount_fen']) / 100, '.2f').rstrip('0').rstrip('.')
    # Strip zeroes only after the decimal point (428.00 -> 428, 400.00 -> 400).
    number = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', invoice['invoice_number'])
    return f'{number}_{seller}_{amount}.pdf'


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
    """A5 landscape, grayscale, one complete invoice per sheet, 5 mm margins."""
    import fitz
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
                page = source[0]
                pixmap = page.get_pixmap(matrix=fitz.Matrix(200 / 72, 200 / 72), colorspace=fitz.csGRAY, alpha=False)
                target = output.new_page(width=width, height=height)
                target.insert_image(fitz.Rect(margin, margin, width - margin, height - margin), pixmap=pixmap, keep_proportion=True)
        output.save(destination, deflate=True)


def print_command(pdf, sumatra=None):
    if sys.platform == 'win32':
        executable = sumatra or shutil.which('SumatraPDF.exe')
        if not executable:
            for base in (os.environ.get('LOCALAPPDATA'), os.environ.get('PROGRAMFILES'), os.environ.get('PROGRAMFILES(X86)')):
                if base and (Path(base) / 'SumatraPDF' / 'SumatraPDF.exe').is_file():
                    executable = str(Path(base) / 'SumatraPDF' / 'SumatraPDF.exe')
                    break
        if not executable:
            raise FileNotFoundError('请安装 SumatraPDF，或在下一步选择 SumatraPDF.exe，用于设置 A5 横向黑白打印')
        return [executable, '-print-dialog', '-exit-when-done', '-print-settings', 'paper=A5,landscape,monochrome,fit', str(pdf)]
    executable = shutil.which('lp')
    if not executable:
        raise FileNotFoundError('未找到系统打印命令 lp，请配置打印机后重试')
    return [executable, '-o', 'media=A5', '-o', 'orientation-requested=4', '-o', 'print-color-mode=monochrome',
            '-o', 'fit-to-page', '-o', 'number-up=1', str(pdf)]


def submit_print(command):
    result = subprocess.run(command, capture_output=True, text=True, errors='replace')
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or '打印命令未成功执行')
    # A successful command only means submission, never physical success.
