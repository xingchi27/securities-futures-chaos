# wh6_snapshot.ps1 - 把 WH6 窗口截图并用 Windows OCR 输出词表(供 wh6_ocr_pool.py 解析)
# 用法: powershell -ExecutionPolicy Bypass -File wh6_snapshot.ps1 [-Png out.png] [-Words out_words.txt]
param(
  [string]$Png = "wh6_rank.png",
  [string]$Words = "wh6_words.txt"
)
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Runtime.WindowsRuntime
Add-Type @"
using System;
using System.Runtime.InteropServices;
public struct RECT2 { public int Left, Top, Right, Bottom; }
public class W32B {
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT2 rect);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
}
"@
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($WinRtTask, $ResultType) {
  $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
  $netTask = $asTask.Invoke($null, @($WinRtTask))
  $netTask.Wait(-1) | Out-Null
  $netTask.Result
}
[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder,Windows.Foundation,ContentType=WindowsRuntime] | Out-Null
[Windows.Storage.Streams.RandomAccessStream,Windows.Storage.Streams,ContentType=WindowsRuntime] | Out-Null

$p = Get-Process wh6 | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if (-not $p) { Write-Error "WH6 not running"; exit 1 }
[W32B]::ShowWindow($p.MainWindowHandle, 9) | Out-Null
[W32B]::SetForegroundWindow($p.MainWindowHandle) | Out-Null
Start-Sleep -Milliseconds 900
$r = New-Object RECT2
[W32B]::GetWindowRect($p.MainWindowHandle, [ref]$r) | Out-Null
$w = $r.Right - $r.Left; $h = $r.Bottom - $r.Top
$bmp = New-Object System.Drawing.Bitmap -ArgumentList $w,$h
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($r.Left, $r.Top, 0, 0, $bmp.Size)
$bmp.Save($Png, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()

$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync((Resolve-Path $Png))) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$sbmp = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if (-not $engine) { Write-Error "no OCR engine"; exit 1 }
$result = Await ($engine.RecognizeAsync($sbmp)) ([Windows.Media.Ocr.OcrResult])
$sb = New-Object System.Text.StringBuilder
foreach ($line in $result.Lines) {
  foreach ($wd in $line.Words) {
    $rr = $wd.BoundingRect
    [void]$sb.AppendLine(("{0}`t{1}`t{2}" -f [int]$rr.X, [int]$rr.Y, $wd.Text))
  }
}
Set-Content -Path $Words -Value $sb.ToString() -Encoding UTF8
Write-Output "saved png=$Png words=$Words"
