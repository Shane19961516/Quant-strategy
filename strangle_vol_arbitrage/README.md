# Short Strangle Volatility Arbitrage System

宽跨式期权（Short Strangle）波动率套利决策与监控系统。基于 **Black-76** 对商品/股指期货期权定价，自动筛选高 IV 溢价合约，计算保证金与组合希腊值，并提供 FastAPI + Streamlit 交互大盘。

完整设计规约见 [`CURSOR_SPEC.md`](./CURSOR_SPEC.md)。

## 功能概览

| 模块 | 说明 |
|------|------|
| `core/bs76_engine.py` | Black-76 定价与 Delta/Gamma/Vega/Theta |
| `core/metrics.py` | HV30、IVR、IVP、IV-HV、POP |
| `core/screener.py` | DTE/IV 硬过滤 + Δ≈±0.20 对腿匹配 |
| `core/capital_allocator.py` | 国内期权保证金与最大开仓对数 |
| `api/` | FastAPI：筛选、持仓同步、Greeks、图表 |
| `ui/app.py` | Streamlit 双轴监控大盘 |

## 快速开始

```bash
cd strangle_vol_arbitrage
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 单元 / API 测试
pytest -q

# 启动 API
uvicorn api.main:app --reload --port 8000

# 启动 UI（另一终端）
streamlit run ui/app.py
```

API 文档：<http://127.0.0.1:8000/docs>

## 主要接口

- `POST /api/v1/screener/run` — 运行全市场筛选
- `GET /api/v1/screener/candidates` — 最新筛选结果
- `POST /api/v1/positions/sync` — 导入昨日持仓与当日成交
- `GET /api/v1/portfolio/greeks-summary` — 按品种汇总希腊值与盈亏
- `GET /api/v1/charts/price/{underlying}` — K线 + Call/Put 行权价
- `GET /api/v1/charts/vol/{underlying}` — IV vs HV30

## 筛选硬条件

1. DTE ∈ [30, 45]
2. IVR > 50% 且 IVP > 70%
3. IV − HV30 > 5%
4. Call/Put 选取 |Δ| 最接近 0.20 / −0.20

## 风控

- **Delta 倾斜**：|净Δ| > 0.30 → 对冲/移仓提醒
- **Gamma 暴风区**：DTE < 10 且 |F−K|/F ≤ 3% → 建议平仓
- **保证金上限**：账户占用 > 60% → Screener 拒绝新开仓手数建议

## 配置

- `config/settings.yaml` — 资金、筛选阈值、风控
- `config/margin_rules.json` — 各交易所保证金系数

演示数据由 `data_fetcher/market_data.py` 合成，无需外部行情即可跑通筛选与图表。
