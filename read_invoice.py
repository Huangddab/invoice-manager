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
    
init_db() 
invoice_dir = Path(__file__).resolve().parent / "invoices"
pdf_file = list(invoice_dir.glob("*.pdf"))

if not pdf_file:
    print("invoices 文件夹中没有PDF文件")
# else:
#     pdf_path = pdf_file[0]
#     print("正在读取:",pdf_path.name)
    
#     text =  read_invoice_text(pdf_path)
#     print(text)

#     invoice_num = extract_invoice_number(text)
#     if invoice_num  is None:
#         print("未识别到发票号码，请人工核对")
#     else:
#         print("识别到的发票号码:",invoice_num)

#     total_amount = extract_total_amount(text)
#     if total_amount is None:
#         print("未识别到价税合计，请人工核对")
#     else:
#         print(f"价税合计：{total_amount:.2f} 元")

#     invoice_date = extract_invoice_date(text)
#     print("开票日期:",invoice_date)

#     buyer,seller = extract_company_names(text)
#     print("购买方:",buyer)
#     print("销售方:",seller)


# else:
#     pdf_path = pdf_file[0]
#     invoice = parse_invoice(pdf_path)

#     for field, value in invoice.items():
#         print(f"{field}: {value}")


else:
    invoices = []
    seen_numbers = set()
    
    for pdf_path in pdf_file:
        invoice = parse_invoice(pdf_path)
        invoice_number = invoice["invoice_number"]
        
        if invoice_number is None:
            print(f"需要人工核对，未识别到发票号：{pdf_path.name}")
            continue
        
        if invoice_number in seen_numbers:
            print(f"跳过重复发票：{pdf_path.name}")
            continue
        try:
            saved = save_invoice(invoice)
        except ValueError as error:
            print(f"需要人工核对：{pdf_path.name}，原因：{error}")
            continue

        if not saved:
            print(f"数据库中已存在：{invoice_number}")
            continue
        
        seen_numbers.add(invoice_number)
        invoices.append(invoice)
        print(
            f"{invoice['invoice_number']} | "
            f"{invoice['seller']} | "
            f"{invoice['total_amount']} 元"
        )

    print(f"本次新增 {len(invoices)} 张发票")
