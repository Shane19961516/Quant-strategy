# 从仓库同步最新「基本面分析」Web 到本机桌面目录
# 用法（在已有桌面副本上执行）:
#   powershell -ExecutionPolicy Bypass -File .\从仓库更新.ps1
# 或指定路径:
#   powershell -ExecutionPolicy Bypass -File .\从仓库更新.ps1 -Target "E:\360MoveData\Users\Admin\Desktop\基本面分析\web"

param(
  [string]$Target = ""
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not $Target) {
  $candidates = @(
    (Join-Path $env:USERPROFILE "Desktop\基本面分析\web"),
    "E:\360MoveData\Users\Admin\Desktop\基本面分析\web",
    "D:\360MoveData\Users\Admin\Desktop\基本面分析\web",
    (Join-Path $env:USERPROFILE "OneDrive\Desktop\基本面分析\web")
  )
  foreach ($c in $candidates) {
    if (Test-Path (Join-Path $c "app.py")) { $Target = $c; break }
  }
}

if (-not $Target) {
  Write-Host "未找到桌面 web 目录。请用 -Target 指定，例如:"
  Write-Host '  .\从仓库更新.ps1 -Target "E:\360MoveData\Users\Admin\Desktop\基本面分析\web"'
  exit 1
}

Write-Host "同步源: $ScriptDir"
Write-Host "同步到: $Target"

$items = @(
  "app.py",
  "requirements.txt",
  "services",
  "templates",
  "static",
  "scripts",
  "启动Web分析.bat",
  "启动Web分析.ps1",
  "启动Web分析.sh",
  "README.md"
)

foreach ($item in $items) {
  $src = Join-Path $ScriptDir $item
  if (-not (Test-Path $src)) { continue }
  $dst = Join-Path $Target $item
  if (Test-Path $src -PathType Container) {
    if (Test-Path $dst) { Remove-Item $dst -Recurse -Force }
    Copy-Item $src $dst -Recurse -Force
  } else {
    Copy-Item $src $dst -Force
  }
  Write-Host "  OK $item"
}

Write-Host ""
Write-Host "更新完成。请先关掉旧的 python app.py 窗口，再执行:"
Write-Host "  cd `"$Target`""
Write-Host "  python app.py"
Write-Host "然后打开 http://127.0.0.1:8765"
