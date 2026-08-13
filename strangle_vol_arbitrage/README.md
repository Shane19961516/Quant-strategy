# Short Strangle Volatility Arbitrage + 结算监控

宽跨式期权波动率套利决策系统，并接入**东亚期货客户交易结算日报**：

- **昨日持仓**：上传结算单 `.xls` 自动解析「期权持仓汇总」
- **当日成交**：Web 手动录入（与昨日持仓分表，互不覆盖）
- **实时盈亏**：持仓盯市 + 今日成交盈亏 − 手续费
- **筛选大盘**：BS76 / IVR·IVP / POP / 保证金手数 / 双轴图表

规约见 [`CURSOR_SPEC.md`](./CURSOR_SPEC.md)。样例结算单：`fixtures/settlement_sample_2026-08-12.xls`。

## 启动

```bash
cd strangle_vol_arbitrage
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
# 29 passed（含真实结算单解析与上传→成交→盈亏全链路）
```

## 盈亏口径（简）

- **昨日持仓盯市**：卖 `(结算价−标记价)×手数×乘数`；买相反  
- **今日开仓**：卖 `(成交价−标记价)×手数×乘数`；买相反  
- **合计**：盯市 + 今日成交盈亏 − 今日手续费  
