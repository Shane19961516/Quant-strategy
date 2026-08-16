#Requires -Version 5.1
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$PackRoot = $PSScriptRoot
$SkillSrc = Join-Path $PackRoot "mark-alpha-research"
$Repo = "Shane19961516/Quant-strategy"
$Branch = "cursor/hsbc-us-equity-mark-research-92f6"
$RawBase = "https://raw.githubusercontent.com/$Repo/$Branch/.cursor/skills/mark-alpha-research"

function Get-DesktopPath {
    $desktop = [Environment]::GetFolderPath("Desktop")
    if (-not [string]::IsNullOrWhiteSpace($desktop) -and (Test-Path $desktop)) {
        return $desktop
    }
    $fallback = Join-Path $env:USERPROFILE "Desktop"
    New-Item -ItemType Directory -Force -Path $fallback | Out-Null
    return $fallback
}

function Save-Utf8File {
    param([string]$Url, [string]$Dest)
    New-Item -ItemType Directory -Force -Path (Split-Path $Dest) | Out-Null
    Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $Dest
}

$Desktop = Get-DesktopPath
$DestRoot = Join-Path $Desktop "基本面分析"
$DestSkill = Join-Path $DestRoot "mark-alpha-research"
$CursorSkill = Join-Path $env:USERPROFILE ".cursor\skills\mark-alpha-research"

New-Item -ItemType Directory -Force -Path (Join-Path $DestSkill "scripts") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $CursorSkill "scripts") | Out-Null

$localSkill = Join-Path $SkillSrc "SKILL.md"
$localPdf = Join-Path $SkillSrc "scripts\build_mark_research_pdf.py"

if (Test-Path $localSkill) {
    Copy-Item $localSkill (Join-Path $DestSkill "SKILL.md") -Force
    if (Test-Path $localPdf) {
        Copy-Item $localPdf (Join-Path $DestSkill "scripts\build_mark_research_pdf.py") -Force
    }
} else {
    Save-Utf8File "$RawBase/SKILL.md" (Join-Path $DestSkill "SKILL.md")
    Save-Utf8File "$RawBase/scripts/build_mark_research_pdf.py" (Join-Path $DestSkill "scripts\build_mark_research_pdf.py")
}

Copy-Item (Join-Path $DestSkill "SKILL.md") (Join-Path $CursorSkill "SKILL.md") -Force
$pdfSrc = Join-Path $DestSkill "scripts\build_mark_research_pdf.py"
if (Test-Path $pdfSrc) {
    Copy-Item $pdfSrc (Join-Path $CursorSkill "scripts\build_mark_research_pdf.py") -Force
}

$guide = @"
# 基本面分析

这个文件夹是 MARK Alpha Research 技能的本地副本。

## 怎样调用

桌面上的副本只是备份。Cursor 真正读取的位置是：

``$CursorSkill``

1. 完全退出并重新打开 Cursor（技能在启动时扫描）。
2. 打开任意项目，进入 Agent 对话。
3. 输入：

``````
/mark-alpha-research 研究 APP US Equity，完整买方报告
``````

或：

``````
/mark-alpha-research 研究 HSBC US Equity，完整买方报告
``````

## 目录

- mark-alpha-research/SKILL.md
- mark-alpha-research/scripts/build_mark_research_pdf.py
"@
Set-Content -Path (Join-Path $DestRoot "使用说明.md") -Value $guide -Encoding UTF8

Write-Host ""
Write-Host "已完成。"
Write-Host "桌面文件夹: $DestRoot"
Write-Host "Cursor 调用目录: $CursorSkill"
Write-Host "请完全退出并重新打开 Cursor，然后在 Agent 里输入 /mark-alpha-research"
Write-Host ""
