#!/usr/bin/env bash
set -euo pipefail

PACK_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_SRC="$PACK_ROOT/mark-alpha-research"
REPO="Shane19961516/Quant-strategy"
BRANCH="cursor/hsbc-us-equity-mark-research-92f6"
RAW_BASE="https://raw.githubusercontent.com/${REPO}/${BRANCH}/.cursor/skills/mark-alpha-research"

if [[ -d "$HOME/Desktop" ]]; then
  DESKTOP="$HOME/Desktop"
elif [[ -d "$HOME/桌面" ]]; then
  DESKTOP="$HOME/桌面"
else
  DESKTOP="$HOME/Desktop"
  mkdir -p "$DESKTOP"
fi

DEST_ROOT="$DESKTOP/基本面分析"
DEST_SKILL="$DEST_ROOT/mark-alpha-research"
CURSOR_SKILL="$HOME/.cursor/skills/mark-alpha-research"

mkdir -p "$DEST_SKILL/scripts" "$CURSOR_SKILL/scripts"

if [[ -f "$SKILL_SRC/SKILL.md" ]]; then
  cp "$SKILL_SRC/SKILL.md" "$DEST_SKILL/SKILL.md"
  if [[ -f "$SKILL_SRC/scripts/build_mark_research_pdf.py" ]]; then
    cp "$SKILL_SRC/scripts/build_mark_research_pdf.py" "$DEST_SKILL/scripts/build_mark_research_pdf.py"
  fi
else
  curl -fsSL "$RAW_BASE/SKILL.md" -o "$DEST_SKILL/SKILL.md"
  curl -fsSL "$RAW_BASE/scripts/build_mark_research_pdf.py" -o "$DEST_SKILL/scripts/build_mark_research_pdf.py"
fi

cp "$DEST_SKILL/SKILL.md" "$CURSOR_SKILL/SKILL.md"
if [[ -f "$DEST_SKILL/scripts/build_mark_research_pdf.py" ]]; then
  cp "$DEST_SKILL/scripts/build_mark_research_pdf.py" "$CURSOR_SKILL/scripts/build_mark_research_pdf.py"
fi

cat > "$DEST_ROOT/使用说明.md" <<EOF
# 基本面分析

这个文件夹是 MARK Alpha Research 技能的本地副本。

## 怎样调用

桌面上的副本只是备份。Cursor 真正读取的位置是：

\`$CURSOR_SKILL\`

1. 完全退出并重新打开 Cursor（技能在启动时扫描）。
2. 打开任意项目，进入 Agent 对话。
3. 输入：

\`\`\`
/mark-alpha-research 研究 APP US Equity，完整买方报告
\`\`\`

## 目录

- mark-alpha-research/SKILL.md
- mark-alpha-research/scripts/build_mark_research_pdf.py
EOF

echo
echo "已完成。"
echo "桌面文件夹: $DEST_ROOT"
echo "Cursor 调用目录: $CURSOR_SKILL"
echo "请完全退出并重新打开 Cursor，然后在 Agent 里输入 /mark-alpha-research"
echo
