from pathlib import Path
from pypdf import PdfReader
from decimal import Decimal
from datetime import date
import re
import shutil
import tempfile
import os
from invoice_manager import database
from invoice_manager.database import init_db,save_invoice

# 提取pdf文本
def read_invoice_text(pdf_path):
    reader = PdfReader(pdf_path)
    page_text = []

    for page in reader.pages:
        text = page.extract_text(extraction_mode="layout") or ""
        page_text.append(text)

    return "\n".join(page_text)

def extract_invoice_identity(text):
    """Return a collision-safe key, optional legacy code, and real number."""
    labelled = re.search(r"发\s*票\s*(?:号\s*码|号)\s*[：:]\s*(\d{20}|\d{8})(?!\d)", text)
    if labelled:
        serial = labelled.group(1)
        if len(serial) == 20:
            return serial, "", serial
        code_match = re.search(r"发\s*票\s*代\s*码\s*[：:]\s*(\d{12}|\d{10})(?!\d)", text)
        if code_match:
            code = code_match.group(1)
            return code + serial, code, serial
        return None, "", serial
    match = re.search(r"(?<!\d)(\d{20})(?!\d)", text)
    return (match.group(1), "", match.group(1)) if match else (None, "", "")


def extract_invoice_number(text):
    return extract_invoice_identity(text)[0]

# 解析价税总价
def extract_total_amount(text):
    labelled = re.search(r"[（(]\s*小\s*写\s*[）)]\s*[¥￥]\s*([0-9,]+\.[0-9]{2})(?![0-9])", text)
    if labelled:
        return Decimal(labelled.group(1).replace(',', ''))
    pattern = r"[圆元][^\n¥￥]*[¥￥]\s*([0-9,]+\.[0-9]{2})(?![0-9])"
    match = re.search(pattern, text)

    if match:
        amount_text = match.group(1).replace(",","")
        return Decimal(amount_text)
    return None

# 提取开票日期
def extract_invoice_date(text):
    match = re.search(r"([0-9]{4})\s*年\s*([0-9]{1,2})\s*月\s*([0-9]{1,2})\s*日",text)
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
        # Traditional invoices place buyer and seller on separate name rows.
        names = re.findall(r"^[ \t]*名[ \t]*称[：:][ \t]*([^\n]+)", text, re.MULTILINE)
        if len(names) == 2:
            return tuple(re.split(r"[ \t]{2,}", name.strip())[0].strip() for name in names)
        return None,None

    buyer = match.group(1).strip()
    seller = match.group(2).strip()

    return buyer,seller

# 解析整张发票，返回字典形式的结果
def parse_invoice_text(text, pdf_path):
    buyer, seller = extract_company_names(text)
    number, code, serial = extract_invoice_identity(text)
    return {
        "invoice_number": number,
        "invoice_code": code,
        "invoice_serial": serial,
        "total_amount": extract_total_amount(text),
        "invoice_date": extract_invoice_date(text),
        "buyer": buyer,
        "seller": seller,
        "file_path": str(Path(pdf_path).resolve()),
    }


def parse_invoice(pdf_path):
    pdf_path = Path(pdf_path)
    required = ("invoice_number", "total_amount", "invoice_date", "buyer", "seller")
    candidates = []
    extraction_error = None
    try:
        candidates.append(parse_invoice_text(read_invoice_text(pdf_path), pdf_path))
    except Exception as error:
        extraction_error = error
    if candidates and all(candidates[0][field] is not None and candidates[0][field] != "" for field in required):
        return candidates[0]
    try:
        import fitz
        with fitz.open(pdf_path) as document:
            text = "\n".join(page.get_text(sort=True) for page in document)
        candidates.append(parse_invoice_text(text, pdf_path))
    except Exception as error:
        extraction_error = error
    if not candidates:
        raise ValueError(f"PDF 无法读取：{extraction_error}")
    # Keep fields from one coherent extraction instead of mixing invoice identities.
    return max(candidates, key=lambda row: sum(row[field] is not None and row[field] != "" for field in required))


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
            if not number or not re.fullmatch(r"\d{18}|\d{20}", number):
                raise ValueError("未识别到有效发票号：数电发票需20位号码，传统发票需代码及8位号码")
            required = ("total_amount", "invoice_date", "buyer", "seller")
            names = {"total_amount": "价税合计", "invoice_date": "开票日期", "buyer": "购买方", "seller": "销售方"}
            missing = [names[key] for key in required if invoice.get(key) is None or invoice.get(key) == ""]
            if missing:
                raise ValueError("未识别到：" + "、".join(missing) + "；请核对 PDF 是否有可提取文字")
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

    invoice_dir = database.DB_PATH.parent / "invoices"
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
