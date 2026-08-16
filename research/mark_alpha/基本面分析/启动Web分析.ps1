#Requires -Version 5.1
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Set-Location -LiteralPath $PSScriptRoot

Write-Host "========================================"
Write-Host "  基本面分析 Web 启动器"
Write-Host "  http://127.0.0.1:8765"
Write-Host "========================================"

function Find-Python {
    $candidates = @(
        @{ File = "py"; Args = @("-3") },
        @{ File = "python"; Args = @() },
        @{ File = "python3"; Args = @() }
    )
    foreach ($c in $candidates) {
        $cmd = Get-Command $c.File -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        try {
            $allArgs = $c.Args + @("-c", "import sys; print(sys.executable); print(sys.version)")
            $out = & $c.File @allArgs 2>$null
            if ($LASTEXITCODE -eq 0 -and $out) {
                return @{ File = $c.File; Args = $c.Args; Exe = ($out | Select-Object -First 1) }
            }
        } catch { }
    }
    return $null
}

$py = Find-Python
if (-not $py) {
    Write-Host "[错误] 未检测到可用 Python。" -ForegroundColor Red
    Write-Host "请安装 https://www.python.org/downloads/ 并勾选 Add python.exe to PATH"
    Read-Host "按回车退出"
    exit 1
}

Write-Host "Python: $($py.Exe)"
Write-Host "安装依赖..."
& $py.File (@($py.Args) + @("-m", "pip", "install", "-r", "requirements.txt"))
if ($LASTEXITCODE -ne 0) {
    Write-Host "[错误] pip 安装失败" -ForegroundColor Red
    Read-Host "按回车退出"
    exit 1
}

# Kill old instance on 8765 if any
try {
    $conn = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
    if ($conn) {
        $conn | ForEach-Object {
            try { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue } catch {}
        }
        Start-Sleep -Seconds 1
    }
} catch {}

Write-Host "启动服务中（勿关闭本窗口）..."
$procArgs = @($py.Args) + @("app.py")
$proc = Start-Process -FilePath $py.File -ArgumentList $procArgs -WorkingDirectory $PSScriptRoot -PassThru -WindowStyle Hidden

$ok = $false
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Seconds 1
    if ($proc.HasExited) { break }
    try {
        $r = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:8765/api/health" -TimeoutSec 1
        if ($r.StatusCode -eq 200) { $ok = $true; break }
    } catch {}
}

if (-not $ok) {
    Write-Host "[错误] 服务未能启动。请检查上方/任务管理器中的 python 报错。" -ForegroundColor Red
    if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue }
    # Retry in foreground to show traceback
    Write-Host "改为前台启动以显示错误：" -ForegroundColor Yellow
    & $py.File (@($py.Args) + @("app.py"))
    Read-Host "按回车退出"
    exit 1
}

Start-Process "http://127.0.0.1:8765"
Write-Host "已打开浏览器。关闭本窗口将停止服务。" -ForegroundColor Green
Write-Host "健康检查: http://127.0.0.1:8765/api/health"
try {
    Wait-Process -Id $proc.Id
} finally {
    if (-not $proc.HasExited) {
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    }
}
