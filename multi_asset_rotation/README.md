# 多资产周度轮动策略（股 / 债 / 金 / 美股）— 最终版

面向实盘落地的 ETF 周度权重轮动框架，标的：

| 代码 | 名称 | 角色 |
|---|---|---|
| 159816 | 地方债0-4Y | 安全垫 / 现金替代 |
| 159934 | 黄金ETF | 通胀/避险 |
| 515450 | 红利低波 | A股防御权益 |
| 513500 / 513110 / 513400 | 标普500 / 纳指 / 道指 | 美股 sleeve（择强） |

## 最终版逻辑：信号选方向 + 非对称权重调解 + YTD 油门

### 信号层
1. **每周最后一个交易日收盘**计算信号，**下一交易日（通常周一）开盘调仓**；
2. 美股三只高度相关，先按相对动量选 1 只；
3. **双动量**：风险资产绝对动量需跑赢债券，且价格在均线之上；
4. Top-K 风险资产进入战术池。

### 权重层
1. **战术仓**：合格风险资产 **逆波动加权**，并用 `vol_budget` 缩放风险预算；
2. **战略中枢** `neutral_sleeve`（债/金/A股/美股）；
3. **进攻期**：`sleeve = (1-tilt)*中枢 + tilt*战术`，再做 `max_sleeve_dev` 偏离裁剪；
4. **防守期**（金丝雀 / 无合格资产）：跳过中枢混合，按 `canary_bond_floor` 提高债券，权重即时落地；
5. **YTD 油门**：用影子净值跟踪当年已实现收益——偏高时降低战术倾斜/风险预算并抬升债券；偏低时略增进攻，专门平滑分年收益。

## 回测结果（最终版，cost=2bp，2020-09 → 2026-08）

| 指标 | 最终版 | 旧版硬切换 | 目标 |
|---|---|---|---|
| 年化 | **~15.1%** | ~15.9% | ≥15% |
| Sharpe(rf=0) | **~2.03** | ~2.04 | ≥2 |
| 最大回撤 | **~-5.4%** | ~-5.3% | ≤7% |
| 分年标准差 | **~9.45%** | ~10.9% | 更平滑 |
| 最差分年 | **正收益** | 正收益 | — |

> 历史回测不代表未来收益；美股 ETF 有溢价/折价与隔夜跳空，实盘请用限价/分批。

## 快速运行

```bash
cd multi_asset_rotation
pip install -r requirements.txt
python run.py                 # 主回测 + 图表 + 版本对比
python compare_versions.py    # 仅对比 v1 vs 最终版
python optimize_mediation.py  # 可选：权重层搜索
```

输出目录：`multi_asset_rotation/output/`

- `nav_curve.png` / `drawdown.png` / `weights.png` / `attribution.png`
- `yearly_returns_bar.png` / `yearly_compare_v1_vs_final.png`
- `monthly_heatmap.png` / `month_seasonality.png`
- `summary.json` / `version_compare.json`
- `trades.csv` / `weights_signal_friday.csv`

## 数据说明

- 数据源：`akshare`（优先 `stock_zh_a_hist_tx` 前复权，失败回退 `fund_etf_hist_sina`）
- 缓存：`multi_asset_rotation/data/{code}.csv`

## 最终版关键参数

见 `config.py`：

```python
vol_budget=0.078
neutral_sleeve={bond:0.30, gold:0.23, cn:0.14, us:0.33}
active_tilt=0.90
max_sleeve_dev=0.40
canary_bond_floor=0.95
ytd_soft_cap=0.08
ytd_dampen=0.60
rebalance_thresh=0.25
cost_bps=2.0
```

## 实盘执行清单

1. 每周五收盘后运行 `python run.py`，读取最新信号权重；
2. 下周一开盘按目标权重做**差额调仓**；
3. QDII（美股/黄金）建议限价单；
4. 单边成本默认 2bp，可按券商佣金修改 `cost_bps`。
