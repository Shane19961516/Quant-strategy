#!/usr/bin/env bash
set -euo pipefail

PACK_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="Shane19961516/Quant-strategy"
BRANCH="cursor/mark-research-688008-d5ea"

if [[ -d "$HOME/Desktop" ]]; then
  DESKTOP="$HOME/Desktop"
elif [[ -d "$HOME/桌面" ]]; then
  DESKTOP="$HOME/桌面"
else
  DESKTOP="$HOME/Desktop"
  mkdir -p "$DESKTOP"
fi

DEST_ROOT="$DESKTOP/基本面分析"
DEST_WEB="$DEST_ROOT/web"
mkdir -p "$DEST_WEB"

if [[ -f "$PACK_ROOT/app.py" ]]; then
  cp -R "$PACK_ROOT"/. "$DEST_WEB"/
else
  TMP="$(mktemp -d)"
  curl -fsSL "https://codeload.github.com/${REPO}/zip/refs/heads/${BRANCH}" -o "$TMP/pack.zip"
  unzip -q "$TMP/pack.zip" -d "$TMP/extract"
  SRC="$(find "$TMP/extract" -type d -name '基本面分析' | head -n 1)"
  cp -R "$SRC"/. "$DEST_WEB"/
  rm -rf "$TMP"
fi

chmod +x "$DEST_WEB/启动Web分析.sh" || true

cat > "$DEST_ROOT/打开基本面分析.command" <<EOF
#!/bin/bash
cd "$DEST_WEB"
exec bash "./启动Web分析.sh"
EOF
chmod +x "$DEST_ROOT/打开基本面分析.command"

cat > "$DEST_ROOT/基本面分析Web.url" <<EOF
[InternetShortcut]
URL=http://127.0.0.1:8765
EOF

cp "$DEST_ROOT/打开基本面分析.command" "$DESKTOP/基本面分析.command"
chmod +x "$DESKTOP/基本面分析.command"

cat > "$DEST_ROOT/使用说明.md" <<EOF
# 基本面分析（本地 Web）

## 怎么打开

1. 双击桌面上的 **基本面分析.command**
2. 浏览器打开 http://127.0.0.1:8765
3. 也可双击本文件夹里的 **基本面分析Web.url**（需先启动服务）

## 怎么用

- 输入 TSLA / 688008 / 0700 / 6809.HK
- 自动识别市场，生成 K 线与估值快报
- 导出 Markdown 或 PDF

完整定性研报请用 Cursor：\`/mark-alpha-research\`
EOF

echo
echo "已安装到: $DEST_ROOT"
echo "桌面快捷方式: $DESKTOP/基本面分析.command"
echo
