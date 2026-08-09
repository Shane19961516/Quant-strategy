# 多资产周度轮动策略（股 / 债 / 金 / 美股）— 最终版

面向实盘落地的 ETF 周度权重轮动框架，标的：

| 代码 | 名称 | 角色 |
|---|---|---|
| 159816 | 地方债0-4Y | 安全垫 / 现金替代 |
| 159934 | 黄金ETF | 通胀/避险 |
| 515450 | 红利低波 | A股防御权益 |
| 513500 / 513110 / 513400 | 标普500 / 纳指 / 道指 | 美股 sleeve（择强） |

## 最终版逻辑：信号选方向 + 非对称权重调解

### 信号层
1. **每周最后一个交易日收盘**计算信号，**下一交易日（通常周一）开盘调仓**；
2. 美股三只高度相关，先按相对动量选 1 只；
3. **双动量**：风险资产绝对动量需跑赢债券，且价格在均线之上；
4. Top-K 风险资产进入战术池。

### 权重层（本次最终优化核心）
1. **战术仓**：合格风险资产 **逆波动加权**，并用 `vol_budget` 缩放风险预算；
2. **战略中枢** `neutral_sleeve`（债/金/A股/美股）；
3. **进攻期非对称调解**：`sleeve = (1-tilt)*中枢 + tilt*战术`，再做 `max_sleeve_dev` 偏离裁剪，抑制牛市过度集中；
4. **防守期（金丝雀 / 无合格资产）**：跳过中枢混合，按 `canary_bond_floor` 提高债券，**不强制保留中枢风险敞口**；
5. 防守信号触发时权重 **即时落地**（避免 EMA 残留风险仓）；进攻期可按 `weight_ema` / 换手阈值平滑。

> 关键旧版“金丝雀=100%债券、无中枢约束”不同，最终版把风控与平滑都放到**权重调解层**，而不是叠加更多防守开关。

## 回测结果（最终版，cost=2bp，2020-09 → 2026-08）

典型固化参数下：

- 年化收益 **≥ 15%**
- Sharpe（rf=0）**≥ 2**
- 最大回撤 **≤ 7%**
- 分年收益全非负，且分年标准差低于旧版硬切换方案

> 历史回测不代表未来收益；美股 ETF 有溢价/折价与隔夜跳空，实盘请用限价/分批。

## 快速运行

```bash
cd multi_asset_rotation
pip install -r requirements.txt
python run.py                 # 使用缓存/自动下载
python run.py --force-download  # 强制重拉 akshare 数据
python optimize_mediation.py  # 可选：权重层网格/随机搜索
```

输出目录：`multi_asset_rotation/output/`

- `nav_curve.png` 净值曲线
- `drawdown.png` 回撤
- `weights.png` 仓位
- `attribution.png` 贡献归因
- `yearly_returns_bar.png` 分年收益
- `monthly_heatmap.png` 月度热力图
- `summary.json` 核心指标
- `trades.csv` / `weights_signal_friday.csv` 调仓与信号

## 数据说明

- 数据源：`akshare`（优先 `stock_zh_a_hist_tx` 前复权，失败回退 `fund_etf_hist_sina`）
- 缓存：`multi_asset_rotation/data/{code}.csv`

## 最终版关键参数

见 `config.py` 中 `PARAMS`：

```python
vol_budget=0.0715
neutral_sleeve={bond:0.30, gold:0.23, cn:0.14, us:0.33}
active_tilt=0.91
max_sleeve_dev=0.53
canary_bond_floor=0.90
min_bond=0.07          # 仅进攻期
defense_skip_center=True
rebalance_thresh=0.20
cost_bps=2.0
```

## 实盘执行清单

1. 每周五收盘后运行 `python run.py`，读取最新信号权重；
2. 下周一开盘按目标权重做**差额调仓**（不是清仓重买）；
3. 建议对 QDII（美股/黄金）使用限价单，避免开盘冲击；
4. 单边成本按 2bp 计量，可按券商佣金修改 `cost_bps`。
