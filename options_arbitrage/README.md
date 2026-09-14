# 竞跨式 · 结算与波动率套利监控台

交付形态：**结算昨仓 + 自动行情 + 实时盈亏/希腊值**，对齐 Libra 夜盘口径。

## 一键启动

```bash
cd options_arbitrage
cp .env.example .env   # 填写 CFMMC_PASSWORD；QUOTE_PROVIDER=akshare|ctp
pip install -r requirements.txt

# API（启动即自动：清今日手录 → CFMMC(有密码) → 行情；之后每 2 分钟刷行情）
uvicorn api.main:app --host 0.0.0.0 --port 8000

# Web
streamlit run ui/app.py --server.port 8501
```

## 交付路径（无需日常手搓行情）

1. **结算单**
   - 路径 A：`.env` 配 `CFMMC_USER` / `CFMMC_PASSWORD` → 启动与每个工作日 16:30 自动拉「逐日盯市」
   - 路径 B：Web「①结算单导入」手动上传 `.xls`
2. **最新价 / 昨收**
   - 默认 **akshare** 自动同步（可用 `QUOTE_PROVIDER=ctp`）
   - 夜盘 21:00 后：**昨收 = 当天下午 15:00 日盘收盘价**（不是结算价）
   - 无夜盘品种（AP/JD）夜盘不写最新价 → 浮动盈亏 = 0
3. **当日成交**（可选）：Web「②当日成交」手录；API 每次启动清空，避免脏数据

## 盈亏 / 希腊值口径

- 套利损益 = **昨日持仓损益 + 今日成交损益**（方向计，不冲抵）
- 昨仓：`方向 × 数量 × (最新 − 昨收) × 乘数`；昨收优先 `__PREV_CLOSE__`（日盘收盘）
- 无有效最新价 → 浮动 = 0
- Greeks：BS76，`T=DTE/245`，`r=0`；展示 Θ 为年化，另给日 Θ 现金；无最新价腿希腊值 = 0

## 关键 API

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/settlement/upload` | 手动上传结算单 |
| POST | `/api/v1/settlement/cfmmc-sync` | 监控中心登录拉取 |
| POST | `/api/v1/settlement/auto-feed` | 手动触发自动馈送 |
| GET | `/api/v1/settlement/feed-status` | 馈送状态 / 价格口径 |
| POST | `/api/v1/settlement/sync-quotes` | 拉行情写入 marks |
| GET | `/api/v1/settlement/live-pnl` | 实时盈亏 |
| GET | `/api/v1/settlement/risk-cockpit` | 风控台 |
| GET | `/health` | 健康检查 + feed |

## 测试

```bash
AUTO_QUOTES=0 AUTO_CFMMC=0 AUTO_SCHEDULER=0 pytest -q
```
