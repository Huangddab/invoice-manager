from pathlib import Path
from   pypdf import PdfReader
from decimal import Decimal
from datetime import date
import re
import shutil
import tempfile
import os
import database
from database import init_db,save_invoice

# 提取pdf文本
def read_invoice_text(pdf_path):
    reader = PdfReader(pdf_path)
    page_text = []

    for page in reader.pages:
        # text = page.extract_text() or ""
        text = page.extract_text(extraction_mode="layout") or ""
        page_text.append(text)

    return "\n".join(page_text)

# 解析发票号
def extract_invoice_number(text):
    match = re.search(r"(?<!\d)(\d{20})(?!\d)", text)
    if match:
        return match.group(1)
    return None

# 解析价税总价
def extract_total_amount(text):
    pattern = r"[圆元][^\n¥￥]*[¥￥]\s*([0-9,]+\.[0-9]{2})(?![0-9])"
    match = re.search(pattern, text)

    if match:
        amount_text = match.group(1).replace(",","")
        return Decimal(amount_text)
    return None

# 提取开票日期
def extract_invoice_date(text):
    match = re.search(r"([0-9]{4})年\s*([0-9]{1,2})月\s*([0-9]{1,2})日",text)
    if match is None:
        return None

    year = int(match.group(1))
    month = int(match.group(2))
    day = int(match.group(3))
    try:
        return date(year,month,day)
    except ValueError:
        return None

# 提取购买方和销售方
def extract_company_names(text):
    pattern = r"购\s*名称[：:]\s*(.*?)\s+销\s*名称[：:]\s*([^\n]+)"
    match = re.search(pattern,text)
     # 兼容销售方名称被提取到“销 名称”标签前面的情况
    if match is None:
        fallback_pattern = (
            r"购[ \t]*名称[：:][ \t]*([^\n]+?)"
            r"[ \t]{2,}([^\n]+?)"
            r"销[ \t]*名称[：:][ \t]*(?=\n|$)"
        )
        match = re.search(fallback_pattern, text)

    if match is None:
        return None,None

    buyer = match.group(1).strip()
    seller = match.group(2).strip()

    return buyer,seller

# 解析整张发票，返回字典形式的结果
def parse_invoice(pdf_path):
    text = read_invoice_text(pdf_path)
    buyer,seller = extract_company_names(text)
    return{
        "invoice_number": extract_invoice_number(text),
        "total_amount": extract_total_amount(text),
        "invoice_date": extract_invoice_date(text),
        "buyer": buyer,
        "seller": seller,
        "file_path": str(pdf_path.resolve()),
    }

def import_invoices(paths, category="meal"):
    """Validate and keep an original byte-for-byte copy before indexing."""
    if category not in ('meal', 'other'):
        raise ValueError('无效的发票分类')
    result = {"added": 0, "duplicates": 0, "errors": []}
    for path in paths:
        path = Path(path)
        try:
            invoice = parse_invoice(path)
            invoice["category"] = category
            number = invoice["invoice_number"]
            if not number or not re.fullmatch(r"\d{20}", number):
                raise ValueError("未识别到有效的20位发票号")
            required = ("total_amount", "invoice_date", "buyer", "seller")
            if any(invoice.get(key) is None or invoice.get(key) == "" for key in required):
                raise ValueError("发票信息不完整，请核对金额、日期、购买方和销售方")
            directory = database.DB_PATH.parent / "managed_pdfs"
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / f"{number}.pdf"
            # Existing managed originals are immutable; renamed duplicates never replace them.
            if not target.exists():
                handle, temporary = tempfile.mkstemp(dir=directory, suffix=".pdf")
                os.close(handle)
                try:
                    shutil.copyfile(path, temporary)
                    os.replace(temporary, target)
                finally:
                    Path(temporary).unlink(missing_ok=True)
            invoice["file_path"] = str(target.resolve())
            if save_invoice(invoice):
                result["added"] += 1
            else:
                database.relink_invoice_file(number, str(target.resolve()))
                result["duplicates"] += 1
        except Exception as error:
            result["errors"].append((path.name, str(error)))
    return result


# 扫描发票文件夹，解析并保存发票信息
def scan_invoice_folder():
    init_db()

    invoice_dir = Path(__file__).resolve().parent / "invoices"
    invoice_dir.mkdir(parents=True, exist_ok=True)

    pdf_files = sorted(
        path
        for path in invoice_dir.iterdir()
        if path.is_file() and path.suffix.lower() == ".pdf"
    )

    imported = import_invoices(pdf_files)
    return {"added": imported["added"], "duplicate": imported["duplicates"],
            "errors": [f"{name}：{error}" for name, error in imported["errors"]]}


if __name__ == "__main__":
    result = scan_invoice_folder()

    print(
        f"新增 {result['added']} 张，"
        f"重复 {result['duplicate']} 张，"
        f"失败 {len(result['errors'])} 份文件"
    )

    for error in result["errors"]:
        print(error)
