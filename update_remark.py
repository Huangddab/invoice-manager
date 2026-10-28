from database import init_db, update_remark


init_db()

invoice_number = input("请输入发票号码：").strip()
remark = input("请输入备注：").strip()

if update_remark(invoice_number, remark):
    print("备注保存成功")
else:
    print("未找到这张发票")