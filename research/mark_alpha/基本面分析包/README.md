# 把 MARK 技能装到本机桌面，并让 Cursor 可以调用

云端 Agent 写不到你的电脑桌面。在本机运行安装脚本后，会同时做两件事：

1. 在桌面新建文件夹 **基本面分析**（你要的本地副本）
2. 把同一套文件装进 Cursor 全局技能目录，这样才能在任意项目里输入 `/mark-alpha-research`

只把文件放在桌面不够。Cursor 只扫描：

- 全局：`~/.cursor/skills/mark-alpha-research/SKILL.md`
- 当前仓库：`.cursor/skills/mark-alpha-research/SKILL.md`

## Windows（PowerShell）

在本机打开 PowerShell，粘贴整段后回车：

```powershell
$ErrorActionPreference = "Stop"
$desk = [Environment]::GetFolderPath("Desktop")
$dest = Join-Path $desk "基本面分析\mark-alpha-research"
$cursor = Join-Path $env:USERPROFILE ".cursor\skills\mark-alpha-research"
New-Item -ItemType Directory -Force -Path "$dest\scripts", "$cursor\scripts" | Out-Null
$base = "https://raw.githubusercontent.com/Shane19961516/Quant-strategy/cursor/hsbc-us-equity-mark-research-92f6/.cursor/skills/mark-alpha-research"
Invoke-WebRequest -UseBasicParsing "$base/SKILL.md" -OutFile "$dest\SKILL.md"
Invoke-WebRequest -UseBasicParsing "$base/scripts/build_mark_research_pdf.py" -OutFile "$dest\scripts\build_mark_research_pdf.py"
Copy-Item "$dest\SKILL.md" "$cursor\SKILL.md" -Force
Copy-Item "$dest\scripts\build_mark_research_pdf.py" "$cursor\scripts\build_mark_research_pdf.py" -Force
Set-Content -Encoding UTF8 (Join-Path $desk "基本面分析\使用说明.md") @"
桌面副本已就绪。请完全退出并重新打开 Cursor，在 Agent 里输入：
/mark-alpha-research 研究 APP US Equity，完整买方报告
"@
Write-Host "桌面: $desk\基本面分析"
Write-Host "Cursor: $cursor"
```

如果已经克隆了本仓库，也可以在资源管理器里进入 `research/mark_alpha/基本面分析包/`，右键 `安装到本地桌面.ps1` → 用 PowerShell 运行。

## macOS / Linux

```bash
bash -c '
set -e
DESKTOP="${HOME}/Desktop"
[ -d "$HOME/桌面" ] && DESKTOP="$HOME/桌面"
DEST="$DESKTOP/基本面分析/mark-alpha-research"
CURSOR="$HOME/.cursor/skills/mark-alpha-research"
mkdir -p "$DEST/scripts" "$CURSOR/scripts"
BASE="https://raw.githubusercontent.com/Shane19961516/Quant-strategy/cursor/hsbc-us-equity-mark-research-92f6/.cursor/skills/mark-alpha-research"
curl -fsSL "$BASE/SKILL.md" -o "$DEST/SKILL.md"
curl -fsSL "$BASE/scripts/build_mark_research_pdf.py" -o "$DEST/scripts/build_mark_research_pdf.py"
cp "$DEST/SKILL.md" "$CURSOR/SKILL.md"
cp "$DEST/scripts/build_mark_research_pdf.py" "$CURSOR/scripts/build_mark_research_pdf.py"
echo "桌面: $DESKTOP/基本面分析"
echo "Cursor: $CURSOR"
'
```

## 安装后如何调用

1. **完全退出 Cursor 再打开**（技能在启动时扫描）。
2. 任意项目 → Agent 对话。
3. 输入 `/mark-alpha-research` 再跟标的，例如：

```text
/mark-alpha-research 研究 HSBC US Equity，完整买方报告
```

对话里应出现技能名 `mark-alpha-research`。如果没有，检查：

```text
%USERPROFILE%\.cursor\skills\mark-alpha-research\SKILL.md
```

文件必须直接在这个文件夹里，不要多套一层目录。
