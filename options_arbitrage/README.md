# Short Strangle Volatility Arbitrage + 结算监控

宽跨式期权波动率套利决策系统，并接入**东亚期货客户交易结算日报**：

- **昨日持仓**：上传结算单 `.xls` 自动解析「期权持仓汇总」
- **当日成交**：Web 手动录入（与昨日持仓分表，互不覆盖）
- **实时盈亏**：持仓盯市 + 今日成交盈亏 − 手续费
- **筛选大盘**：BS76 / IVR·IVP / POP / 保证金手数 / 双轴图表
- **收盘扫描**：`run_daily_scan.py` 按技能流程输出 Markdown 报告（IV / 流动性 / 震荡 / 事件 / 保证金·希腊值·胜率）

规约见 [`CURSOR_SPEC.md`](./CURSOR_SPEC.md)。样例结算单：`fixtures/settlement_sample_2026-08-12.xls`。

## 全流程 E2E（推荐入口）

```bash
cd options_arbitrage
pip install -r requirements.txt

# 1) 回填 ≥252 日 ATM IV（CZCE 交易所 / DCE 可用 user_csv）
python scripts/seed_iv_history.py --days 320 --products SR,CF,TA,MA,RM,OI

# 2) 一带账户参数跑通下一交易日扫描
python run_e2e.py --equity 500000 --client-margin-addon 0.05

# 离线快照
python run_e2e.py --csv-dir ./data/snapshots/20260820 --equity 500000 --client-margin-addon 0.05
```

输出：`output/next_session_report.md`、`output/next_session_scan.json`  
文档：`docs/方法与口径.md`、`docs/交易所规则管理.md`、`docs/报告规范.md`、`docs/数据规范.md`

## 收盘后 / 下一交易日扫描（v2）

```bash
cd options_arbitrage
python run_next_day_scan.py
python run_next_day_scan.py --equity 500000 --client-margin-addon 0.05
```

输出：`output/next_session_report.md`（**下一交易日候选**，非即时成交声明）

规范文档：`docs/方法与口径.md`、`docs/交易所规则管理.md`、`docs/报告规范.md`

## 收盘后扫描（v1 简化版）

```bash
cd options_arbitrage
pip install -r requirements.txt

# 优先 AkShare 实盘链；失败自动回退演示数据
python run_daily_scan.py

# 仅演示数据 / 强制实盘 / 关闭事件过滤
python run_daily_scan.py --demo-only
python run_daily_scan.py --live
python run_daily_scan.py --no-events --relax-technicals
```

报告输出：`output/latest_report.md`、`output/latest_scan.json`。

默认阈值（可在 `config/settings.yaml` 修改）：IV Rank≥60 或 IV Percentile≥70，DTE 30–60，Δ∈[0.15,0.20]，权/保比≥8%，胜率≥70%。

## 启动

```bash
cd options_arbitrage
pip install -r requirements.txt

# 终端 1 — API
uvicorn api.main:app --reload --port 8000

# 终端 2 — Web
streamlit run ui/app.py
```

- API 文档：http://127.0.0.1:8000/docs  
- Web：侧边栏切换「结算单导入 / 当日成交录入 / 实时盈亏监控 / 筛选大盘」

## 工作流

1. 上传**昨日**结算单 → 解析资金权益、保证金、期权持仓（82 手卖持仓等）
2. 在「当日成交」页手录今日开/平仓（不会写入昨日持仓表）
3. 「实时盈亏」页调标记价格，查看总盈亏与估算权益
4. 「筛选大盘」结合保证金占用做新开仓建议（占用 >60% 拒绝手数）

## 关键结算 API

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/settlement/upload` | 上传昨日结算单 |
| GET | `/api/v1/settlement/yesterday-positions` | 昨日持仓 |
| POST/GET/DELETE | `/api/v1/settlement/today-trades` | 当日成交 CRUD |
| POST | `/api/v1/settlement/marks` | 更新标记价 |
| GET | `/api/v1/settlement/live-pnl` | 实时盈亏汇总 |

## 测试

```bash
pytest -q
```

## 盈亏口径（简）

- **昨日持仓盯市**：卖 `(结算价−标记价)×手数×乘数`；买相反  
- **今日开仓**：卖 `(成交价−标记价)×手数×乘数`；买相反  
- **合计**：盯市 + 今日成交盈亏 − 今日手续费  
