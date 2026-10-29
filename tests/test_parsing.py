from datetime import date
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import fitz
import database
from invoice_files import display_invoice_number, export_name
from read_invoice import extract_invoice_identity, extract_total_amount, import_invoices, parse_invoice, parse_invoice_text


LEGACY = '''深圳电子普通发票
发票代码：100000000001
发票号码：00000001
开票日期：2026 年 05 月 22 日
   名    称：示例购买公司      密    123456
合计 ¥341.51 ¥20.49
价税合计（大写） 叁佰陆拾贰圆整 （小写）¥362.00
   名    称：示例餐饮公司
'''


class ParsingTests(unittest.TestCase):
    def test_legacy_identity_companies_and_spaced_date(self):
        row = parse_invoice_text(LEGACY, Path('legacy.pdf'))
        self.assertEqual(row['invoice_number'], '10000000000100000001')
        self.assertEqual(row['invoice_code'], '100000000001')
        self.assertEqual(row['invoice_serial'], '00000001')
        self.assertEqual(row['invoice_date'], date(2026, 5, 22))
        self.assertEqual(row['total_amount'], Decimal('362.00'))
        self.assertEqual((row['buyer'], row['seller']), ('示例购买公司', '示例餐饮公司'))
        row['total_amount_fen'] = 36200
        self.assertEqual(export_name(row), '00000001_示例餐饮公司_362.pdf')
        self.assertIn('代码100000000001', display_invoice_number(row))

    def test_labelled_number_precedes_unrelated_digits(self):
        identity = extract_invoice_identity('99999999999999999999\n发票号码：26000000000000000001')
        self.assertEqual(identity, ('26000000000000000001', '', '26000000000000000001'))
        self.assertIsNone(extract_invoice_identity('发票号码：00000001')[0])
        self.assertEqual(extract_invoice_identity('发票代码：1000000000 发票号码：00000001')[0], '100000000000000001')

    def test_total_uses_small_amount_label_not_subtotal(self):
        self.assertEqual(extract_total_amount('合计 ¥341.51 ¥20.49\n（小 写） ￥ 362.00'), Decimal('362.00'))

    def make_pdf(self, path, text):
        with fitz.open() as document:
            page = document.new_page()
            page.insert_text((40, 40), text, fontname='china-s', fontsize=10)
            document.save(path)

    def test_garbled_pypdf_text_falls_back_and_import_deduplicates(self):
        previous = database.DB_PATH
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            database.DB_PATH = directory / 'invoices.db'
            try:
                database.init_db()
                pdf = directory / 'legacy.pdf'
                self.make_pdf(pdf, LEGACY)
                with patch('read_invoice.read_invoice_text', return_value='乱码 362.00'):
                    row = parse_invoice(pdf)
                    self.assertEqual(row['total_amount'], Decimal('362.00'))
                    self.assertEqual(row['seller'], '示例餐饮公司')
                    result = import_invoices([pdf])
                    self.assertEqual((result['added'], result['errors']), (1, []))
                    renamed = directory / 'renamed.pdf'
                    renamed.write_bytes(pdf.read_bytes())
                    self.assertEqual(import_invoices([renamed])['duplicates'], 1)
                    other = directory / 'different_code.pdf'
                    self.make_pdf(other, LEGACY.replace('100000000001', '100000000002'))
                    self.assertEqual(import_invoices([other])['added'], 1)
                rows = database.list_invoices()
                self.assertEqual(len(rows), 2)
                self.assertEqual({row['invoice_serial'] for row in rows}, {'00000001'})
                self.assertEqual({row['invoice_code'] for row in rows}, {'100000000001', '100000000002'})
            finally:
                database.DB_PATH = previous
