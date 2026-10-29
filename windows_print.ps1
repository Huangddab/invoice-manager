# Called as an encoded command by Python; $ImageDirectory is a literal local path.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
if ([System.Drawing.Printing.PrinterSettings]::InstalledPrinters.Count -eq 0) {
    [Console]::Error.WriteLine('Windows 未安装打印机，请先在系统设置添加打印机。')
    exit 1
}
$files = @(Get-ChildItem -LiteralPath $ImageDirectory -Filter '*.png' | Sort-Object Name)
if ($files.Count -eq 0) { throw '未找到打印图像，请重试。' }
$document = New-Object System.Drawing.Printing.PrintDocument
$dialog = New-Object System.Windows.Forms.PrintDialog
$dialog.Document = $document
$dialog.UseEXDialog = $true
$dialog.AllowSomePages = $false
$dialog.AllowSelection = $false
$document.DocumentName = '餐饮发票 A5 横向黑白'
$document.DefaultPageSettings.Landscape = $true
$document.DefaultPageSettings.Color = $false
$document.DefaultPageSettings.Margins = New-Object System.Drawing.Printing.Margins(20, 20, 20, 20)
$defaultA5 = @($document.PrinterSettings.PaperSizes | Where-Object { $_.Kind -eq [System.Drawing.Printing.PaperKind]::A5 })
if ($defaultA5.Count -gt 0) { $document.DefaultPageSettings.PaperSize = $defaultA5[0] }
try {
    if ($dialog.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) { exit 2 }
    $a5 = @($document.PrinterSettings.PaperSizes | Where-Object { $_.Kind -eq [System.Drawing.Printing.PaperKind]::A5 -or $_.PaperName -match '(^|\W)A5($|\W)' })
    if ($a5.Count -eq 0) { throw '所选打印机未提供 A5 纸张，请安装完整驱动或在打印机设置中添加 A5 纸张后重试。' }
    $document.DefaultPageSettings.PaperSize = $a5[0]
    $document.DefaultPageSettings.Landscape = $true
    $document.DefaultPageSettings.Color = $false
    $document.PrinterSettings.Copies = 1
    $script:pageIndex = 0
    $document.add_PrintPage({
        param($sender, $eventArgs)
        $image = [System.Drawing.Image]::FromFile($files[$script:pageIndex].FullName)
        try {
            $bounds = $eventArgs.MarginBounds
            $scale = [Math]::Min($bounds.Width / $image.Width, $bounds.Height / $image.Height)
            $width = [single]($image.Width * $scale)
            $height = [single]($image.Height * $scale)
            $left = [single]($bounds.X + ($bounds.Width - $width) / 2)
            $top = [single]($bounds.Y + ($bounds.Height - $height) / 2)
            $eventArgs.Graphics.DrawImage($image, $left, $top, $width, $height)
            $script:pageIndex++
            $eventArgs.HasMorePages = $script:pageIndex -lt $files.Count
        } finally { $image.Dispose() }
    })
    $document.Print()
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
} finally {
    $dialog.Dispose()
    $document.Dispose()
}
