# 多资产周度轮动策略（股 / 债 / 金 / 美股）

面向实盘落地的 ETF 周度权重轮动框架，标的：

| 代码 | 名称 | 角色 |
|---|---|---|
| 159816 | 地方债0-4Y | 安全垫 / 现金替代 |
| 159934 | 黄金ETF | 通胀/避险 |
| 515450 | 红利低波 | A股防御权益 |
| 513500 / 513110 / 513400 | 标普500 / 纳指 / 道指 | 美股 sleeve（择强） |

## 策略逻辑（校准版 / 年化 15.86%）

1. **每周最后一个交易日收盘**计算信号，**下一交易日（通常周一）开盘调仓**；
2. 美股三只高度相关，先按相对动量选 1 只进入风险池；
3. **双动量**：风险资产绝对动量需跑赢债券，且价格在均线之上；
4. **Top-K + 逆波动加权**，并按组合波动目标缩放（余量进债券）；
5. **金丝雀风控**：风险资产短周期（1周）普遍走弱时，切到 100% 债券；
6. **换手迟滞**：目标权重变化不足阈值则不调，降低无效交易成本。

## 回测结果（校准版，cost=2bp，2020-09 → 2026-08）

- 年化收益 **15.86%**
- Sharpe（rf=0）**2.04**
- 最大回撤 **-5.27%**
- 三项硬指标全部达成（Sharpe≥2 / 年化≥15% / MDD≤7%）

> 历史回测不代表未来收益；美股 ETF 有溢价/折价与隔夜跳空，实盘请用限价/分批。

## 快速运行

```bash
cd multi_asset_rotation
pip install -r requirements.txt
python run.py                 # 使用缓存/自动下载
python run.py --force-download  # 强制重拉 akshare 数据
```

输出目录：`multi_asset_rotation/output/`

- `nav_curve.png` / `drawdown.png` / `weights.png` / `attribution.png`
- `yearly_returns_bar.png`
- `asset_yearly_compare.png` / `asset_yearly_heatmap.png`（同年各资产买入持有收益对比）
- `yearly_contribution_stacked.png` / `yearly_contribution_heatmap.png`（策略持仓分年贡献）
- `latest_signal.json` / `latest_orders.csv`（最新周五信号与差额调仓）
- `REPORT.md` 简明报告
- `monthly_heatmap.png` / `month_seasonality.png`
- `summary.json`
- `trades.csv` / `weights_signal_friday.csv`

## 数据说明

- 数据源：`akshare`（优先 `stock_zh_a_hist_tx` 前复权，失败回退 `fund_etf_hist_sina`）
- 缓存：`multi_asset_rotation/data/{code}.csv`

## 校准参数

见 `config.py` 中 `PARAMS`：

```python
mom_lb=8, abs_lb=4, sma_lb=40, vol_target=0.075,
top_k=2, max_single=0.55, canary_k=3,
rebalance_thresh=0.25, cost_bps=2.0
```

## 实盘执行清单

1. 每周五收盘后运行 `python run.py`，读取最新信号权重；
2. 下周一开盘按目标权重做**差额调仓**；
3. QDII（美股/黄金）建议限价单；
4. 单边成本默认 2bp，可按券商佣金修改 `cost_bps`。
