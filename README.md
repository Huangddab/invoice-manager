# 个人发票管理

使用 Python + Tkinter 管理 PDF 发票，数据保存在项目目录的 `invoices.db`。

## 启动

需要 Python 3.10+ 和 Tkinter（部分 Linux 系统需安装 `python3-tk`）。仓库中已有的 `.venv` 为 Windows 环境，在其他系统请另建虚拟环境。

```sh
python -m pip install -r requirements.txt
python app.py
```

在已配置的云环境中可执行：

```sh
bash /workspace/invoice-manager-support/start.sh
```

这是桌面应用，需要图形显示；没有网页入口。

## 使用

1. 点击「导入 PDF」，可多选文件。重复发票自动跳过，无法解析的文件会显示原因，其他文件继续处理。当前支持包含可提取文本的 PDF，扫描图片需先做 OCR。
2. 输入发票号码、商家或备注进行搜索，结合使用状态、打印状态筛选。金额统计随筛选更新。
3. 点击发票选择；按 Ctrl / Shift 多选，或点击「全选当前列表」。底部显示所选张数和金额。
4. 选择一张后点击「修改备注」，也可双击；选择多张可批量标记打印或设置使用状态。「标记已打印」只记录状态，不会发送到打印机。
5. 按 Ctrl+F 定位搜索，F5 刷新。筛选无结果时可点击「重置筛选」。

导入记录保存原 PDF 路径，不会移动或删除原文件。请保留原文件；备份时同时备份 PDF 和 `invoices.db`。导入失败的发票不会进入列表。

## 验证

```sh
python -m unittest discover -s tests -v
```

测试使用临时数据库，不修改真实发票数据。GUI 测试需要可用显示器；云环境启动虚拟显示器后使用 `DISPLAY=:99` 运行测试。
