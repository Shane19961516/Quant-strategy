#Requires -Version 5.1
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$PackRoot = $PSScriptRoot
$Repo = "Shane19961516/Quant-strategy"
$Branch = "cursor/mark-research-688008-d5ea"

function Get-DesktopPath {
    $desktop = [Environment]::GetFolderPath("Desktop")
    if (-not [string]::IsNullOrWhiteSpace($desktop) -and (Test-Path $desktop)) {
        return $desktop
    }
    $fallback = Join-Path $env:USERPROFILE "Desktop"
    New-Item -ItemType Directory -Force -Path $fallback | Out-Null
    return $fallback
}

$Desktop = Get-DesktopPath
$DestRoot = Join-Path $Desktop "基本面分析"
$DestWeb = Join-Path $DestRoot "web"
$CursorSkill = Join-Path $env:USERPROFILE ".cursor\skills\mark-alpha-research"

New-Item -ItemType Directory -Force -Path $DestWeb | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $CursorSkill "scripts") | Out-Null

# Copy local pack if present; otherwise fetch from GitHub branch.
$hasApp = Test-Path (Join-Path $PackRoot "app.py")
if ($hasApp) {
    Copy-Item -Recurse -Force (Join-Path $PackRoot "*") $DestWeb
} else {
    $zip = Join-Path $env:TEMP "mark-fundamentals-web.zip"
    $url = "https://codeload.github.com/$Repo/zip/refs/heads/$Branch"
    Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $zip
    $extract = Join-Path $env:TEMP "mark-fundamentals-web-extract"
    if (Test-Path $extract) { Remove-Item -Recurse -Force $extract }
    Expand-Archive -Path $zip -DestinationPath $extract -Force
    $src = Get-ChildItem $extract -Directory | Select-Object -First 1
    $webSrc = Join-Path $src.FullName "research\mark_alpha\基本面分析"
    Copy-Item -Recurse -Force (Join-Path $webSrc "*") $DestWeb
}

# Also install Cursor skill if bundled nearby or in copied tree.
$skillCandidates = @(
    (Join-Path $PackRoot "..\基本面分析包\mark-alpha-research"),
    (Join-Path $DestWeb "..\mark-alpha-research"),
    (Join-Path $DestWeb "scripts")
)

$skillMd = Join-Path $PackRoot "..\..\..\.cursor\skills\mark-alpha-research\SKILL.md"
if (Test-Path $skillMd) {
    Copy-Item $skillMd (Join-Path $CursorSkill "SKILL.md") -Force
    $pdf = Join-Path (Split-Path $skillMd) "scripts\build_mark_research_pdf.py"
    if (Test-Path $pdf) {
        Copy-Item $pdf (Join-Path $CursorSkill "scripts\build_mark_research_pdf.py") -Force
    }
}

# Desktop launcher shortcut (.bat on Desktop + .url)
$launcher = Join-Path $DestRoot "打开基本面分析.bat"
@"
@echo off
chcp 65001 >nul
cd /d "$DestWeb"
start "" "$DestWeb\启动Web分析.bat"
"@ | Set-Content -Path $launcher -Encoding ASCII

$urlFile = Join-Path $DestRoot "基本面分析Web.url"
@"
[InternetShortcut]
URL=http://127.0.0.1:8765
IconIndex=0
"@ | Set-Content -Path $urlFile -Encoding ASCII

# Also put a quick shortcut on Desktop root
$deskLauncher = Join-Path $Desktop "基本面分析.bat"
@"
@echo off
chcp 65001 >nul
cd /d "$DestWeb"
call "$DestWeb\启动Web分析.bat"
"@ | Set-Content -Path $deskLauncher -Encoding ASCII

$guide = @"
# 基本面分析（本地 Web）

## 怎么打开

1. 双击桌面上的 **基本面分析.bat**（或本文件夹里的 **打开基本面分析.bat**）
2. 浏览器会打开 http://127.0.0.1:8765
3. 若网页打不开，先等黑窗口里出现服务启动提示，再双击 **基本面分析Web.url**

## 怎么用

- 输入 `TSLA`、`688008`、`0700`、`6809.HK` 等代码
- 自动识别美股 / A股 / 港股
- 生成最新 K 线 + 基本面估值快报
- 点击 **导出 Markdown** 或 **导出 PDF**

## 目录

- web/          本地 Web 应用
- 打开基本面分析.bat
- 基本面分析Web.url

完整定性买方研报仍可用 Cursor：`/mark-alpha-research`
"@
Set-Content -Path (Join-Path $DestRoot "使用说明.md") -Value $guide -Encoding UTF8

Write-Host ""
Write-Host "已安装到桌面文件夹: $DestRoot"
Write-Host "桌面快捷方式: $deskLauncher"
Write-Host "请双击「基本面分析.bat」启动。"
Write-Host ""
