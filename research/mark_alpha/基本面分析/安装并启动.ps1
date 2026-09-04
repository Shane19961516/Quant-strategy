#Requires -Version 5.1
# 下载 + 安装到桌面 + 立即启动。在任意目录粘贴运行即可。
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ZipUrl = "https://codeload.github.com/Shane19961516/Quant-strategy/zip/refs/heads/cursor/mark-research-688008-d5ea"

function Get-DesktopPath {
    $candidates = @(
        [Environment]::GetFolderPath("Desktop"),
        (Join-Path $env:USERPROFILE "Desktop"),
        (Join-Path $env:USERPROFILE "桌面"),
        (Join-Path $env:USERPROFILE "OneDrive\Desktop"),
        (Join-Path $env:USERPROFILE "OneDrive\桌面")
    ) | Where-Object { $_ -and (Test-Path $_) }
    if ($candidates.Count -gt 0) { return $candidates[0] }
    $fallback = Join-Path $env:USERPROFILE "Desktop"
    New-Item -ItemType Directory -Force -Path $fallback | Out-Null
    return $fallback
}

$Desktop = Get-DesktopPath
Write-Host "检测到桌面: $Desktop" -ForegroundColor Cyan

$tmp = Join-Path $env:TEMP ("mark-web-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
$zip = Join-Path $tmp "pack.zip"

Write-Host "正在下载安装包..." -ForegroundColor Cyan
Invoke-WebRequest -UseBasicParsing -Uri $ZipUrl -OutFile $zip
Expand-Archive -Path $zip -DestinationPath $tmp -Force

$webSrc = Get-ChildItem -Path $tmp -Recurse -Directory -Filter "基本面分析" |
    Where-Object { Test-Path (Join-Path $_.FullName "app.py") } |
    Select-Object -First 1
if (-not $webSrc) { throw "下载包中找不到 基本面分析/app.py" }

$DestRoot = Join-Path $Desktop "基本面分析"
$DestWeb = Join-Path $DestRoot "web"
if (Test-Path $DestWeb) { Remove-Item -Recurse -Force $DestWeb }
New-Item -ItemType Directory -Force -Path $DestWeb | Out-Null
Copy-Item -Recurse -Force (Join-Path $webSrc.FullName "*") $DestWeb

$deskBat = Join-Path $Desktop "基本面分析.bat"
@"
@echo off
chcp 65001 >nul
cd /d "$DestWeb"
powershell -NoProfile -ExecutionPolicy Bypass -File "$DestWeb\启动Web分析.ps1"
if errorlevel 1 pause
"@ | Set-Content -Path $deskBat -Encoding ASCII

@"
[InternetShortcut]
URL=http://127.0.0.1:8765
"@ | Set-Content -Path (Join-Path $DestRoot "基本面分析Web.url") -Encoding ASCII

Remove-Item -Recurse -Force $tmp

Write-Host "安装完成: $DestWeb" -ForegroundColor Green
Write-Host "正在安装 Python 依赖并启动服务..." -ForegroundColor Cyan
Set-Location -LiteralPath $DestWeb
python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "pip 安装失败" }

Write-Host ""
Write-Host "服务启动后请打开: http://127.0.0.1:8765" -ForegroundColor Green
Write-Host "保持本窗口开启。按 Ctrl+C 可停止。" -ForegroundColor Yellow
Write-Host ""
python app.py
