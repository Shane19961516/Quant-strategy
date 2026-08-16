#Requires -Version 5.1
# 一键安装：无需先 clone 仓库。在任意目录打开 PowerShell，粘贴运行本文件内容即可。
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$Repo = "Shane19961516/Quant-strategy"
$Branch = "cursor/mark-research-688008-d5ea"
$ZipUrl = "https://codeload.github.com/$Repo/zip/refs/heads/$Branch"

function Get-DesktopPath {
    $desktop = [Environment]::GetFolderPath("Desktop")
    if (-not [string]::IsNullOrWhiteSpace($desktop) -and (Test-Path $desktop)) {
        return $desktop
    }
    $fallback = Join-Path $env:USERPROFILE "Desktop"
    New-Item -ItemType Directory -Force -Path $fallback | Out-Null
    return $fallback
}

Write-Host "正在下载基本面分析 Web..." -ForegroundColor Cyan
$tmp = Join-Path $env:TEMP ("mark-fundamentals-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
$zip = Join-Path $tmp "pack.zip"
Invoke-WebRequest -UseBasicParsing -Uri $ZipUrl -OutFile $zip
Expand-Archive -Path $zip -DestinationPath $tmp -Force

$webSrc = Get-ChildItem -Path $tmp -Recurse -Directory -Filter "基本面分析" |
    Where-Object { Test-Path (Join-Path $_.FullName "app.py") } |
    Select-Object -First 1

if (-not $webSrc) {
    throw "下载包里找不到 基本面分析/app.py，请检查分支 $Branch"
}

$Desktop = Get-DesktopPath
$DestRoot = Join-Path $Desktop "基本面分析"
$DestWeb = Join-Path $DestRoot "web"
if (Test-Path $DestWeb) { Remove-Item -Recurse -Force $DestWeb }
New-Item -ItemType Directory -Force -Path $DestWeb | Out-Null
Copy-Item -Recurse -Force (Join-Path $webSrc.FullName "*") $DestWeb

$launcher = Join-Path $DestRoot "打开基本面分析.bat"
@"
@echo off
chcp 65001 >nul
cd /d "$DestWeb"
call "$DestWeb\启动Web分析.bat"
"@ | Set-Content -Path $launcher -Encoding ASCII

$urlFile = Join-Path $DestRoot "基本面分析Web.url"
@"
[InternetShortcut]
URL=http://127.0.0.1:8765
"@ | Set-Content -Path $urlFile -Encoding ASCII

$deskLauncher = Join-Path $Desktop "基本面分析.bat"
@"
@echo off
chcp 65001 >nul
cd /d "$DestWeb"
if exist "$DestWeb\启动Web分析.ps1" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "$DestWeb\启动Web分析.ps1"
) else (
  call "$DestWeb\启动Web分析.bat"
)
if errorlevel 1 pause
"@ | Set-Content -Path $deskLauncher -Encoding ASCII

@"
# 基本面分析（本地 Web）

## 打开方式
双击桌面上的 **基本面分析.bat**（会启动服务并打开浏览器）

不要只点「基本面分析Web.url」——那只是网址，服务没启动时会显示“拒绝连接”。

## 用法
输入 TSLA / 688008 / 0700 / 6809.HK

## 若打不开
1. 看黑色启动窗口有无红色报错
2. 确认已安装 Python 3.10+ 并勾选 Add python.exe to PATH
3. 访问 http://127.0.0.1:8765/api/health 应返回 ok
"@ | Set-Content -Path (Join-Path $DestRoot "使用说明.md") -Encoding UTF8

Remove-Item -Recurse -Force $tmp

Write-Host ""
Write-Host "安装完成。" -ForegroundColor Green
Write-Host "桌面文件夹: $DestRoot"
Write-Host "请双击桌面上的「基本面分析.bat」启动（不要只点 .url）。"
Write-Host ""
