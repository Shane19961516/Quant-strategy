# 基本面分析 · 本地 Web

云端 Agent **写不到**你的电脑桌面。把本文件夹安装到本机后，桌面会有快捷方式。

## 一键装到桌面

### Windows

在本机打开 PowerShell，进入本目录后执行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\安装到本地桌面.ps1
```

或在资源管理器中右键 `安装到本地桌面.ps1` → 用 PowerShell 运行。

### macOS / Linux

```bash
bash ./安装到本地桌面.sh
```

安装后桌面会出现：

- `基本面分析/` 文件夹
- `基本面分析.bat`（Windows）或 `基本面分析.command`（macOS）
- 文件夹内 `基本面分析Web.url` → http://127.0.0.1:8765

## 使用

1. 双击桌面快捷方式启动本地服务  
2. 浏览器打开后输入：`TSLA`、`688008`、`0700`、`6809.HK`  
3. 自动识别美股 / A股 / 港股  
4. 查看 K 线 + 基本面估值快报  
5. 点击 **导出 Markdown** 或 **导出 PDF**

## 本机直接启动（不安装）

```bash
cd research/mark_alpha/基本面分析
python3 -m pip install -r requirements.txt
python3 app.py
```

然后访问 http://127.0.0.1:8765

## 说明

- K 线与财务快照来自 Yahoo Finance 公开接口  
- 报告是 MARK 框架的**数据层快报**（估值、情景、仓位启发式）  
- 完整定性买方研报（不可逆变化、证伪、组合角色）请在 Cursor 使用：

```text
/mark-alpha-research 研究 688008 CH Equity，完整买方报告
```
