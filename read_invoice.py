from pathlib import Path
from   pypdf import PdfReader
from decimal import Decimal
from datetime import date
import re
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
    
    if match is None:
        return None,None
        
    buyer = match.group(1).strip()
    seller = match.group(2).strip()
    
    return buyer,seller

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
    

def import_invoices(paths):
    """Import independently so one unreadable PDF does not stop the batch."""
    result = {"added": 0, "duplicates": 0, "errors": []}
    for path in paths:
        path = Path(path)
        try:
            invoice = parse_invoice(path)
            if save_invoice(invoice):
                result["added"] += 1
            else:
                result["duplicates"] += 1
        except Exception as error:
            result["errors"].append((path.name, str(error)))
    return result


if __name__ == "__main__":
    init_db()
    paths = sorted((Path(__file__).resolve().parent / "invoices").glob("*.pdf"))
    result = import_invoices(paths)
    print(f"新增 {result['added']} 张，重复 {result['duplicates']} 张，失败 {len(result['errors'])} 张")
    for name, error in result["errors"]:
        print(f"{name}: {error}")
