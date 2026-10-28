from database import init_db, update_printed


init_db()

invoice_number = input("请输入发票号码：").strip()
choice = input("打印状态（1 已打印，0 未打印）：").strip()

if choice not in ("0", "1"):
    print("输入错误，只能输入 0 或 1")
else:
    is_printed = choice == "1"

    if update_printed(invoice_number, is_printed):
        print("打印状态保存成功")
    else:
        print("未找到这张发票")