# 多资产周度轮动策略（股 / 债 / 金 / 港股 / 美股）

面向实盘落地的 ETF 周度权重轮动框架，标的：

| 代码 | 名称 | 角色 |
|---|---|---|
| 159816 | 地方债0-4Y | 安全垫 / 现金替代 |
| 159934 | 黄金ETF | 通胀/避险 |
| 515450 | 红利低波 | A股防御权益 |
| HK0005（00005.HK） | 汇丰控股 | 港股风险资产（港股通） |
| 513500 / 513110 / 513400 / VIG | 标普500 / 纳指 / 道指 / VIG 红利增长 | 美股 sleeve（择强） |

> 注：513400（道琼斯）上市 2024-02、513110（纳指）上市 2023-03，在其上市前使用各自跟踪指数（.DJI / .NDX）收盘价按比率拼接，使回测从 2020-09 起全程拥有美股 sleeve 数据。

## 策略逻辑（升级版 / 硬目标达成）

1. **每周最后一个交易日收盘**计算信号，**下一交易日（通常周一）开盘调仓**；
2. 美股四只高度相关，先按相对动量选 1 只进入风险池；
3. **双动量 + 趋势**：绝对动量需跑赢债券且 `require_abs_pos`，价格需高于 SMA（含缓冲）；
4. **Top-K + 逆波动加权**，并按组合波动目标缩放；
5. **条件杠杆**：强动量且广度健康、sim 回撤未超 `lev_dd_cap` 时，允许 `max_gross` 至 1.5x（融资成本 `borrow_rate`）；
6. **周度断路器**：策略内模拟净值回撤超 `dd_stop` 强制债券，回撤收窄至 `dd_resume` 后恢复；
7. **日度回撤止损**：仅在杠杆仓位下，组合回撤触发后切债券；高波动时按 `stop_vol_mult` 收紧阈值；**下次再平衡自动恢复**；
8. **金丝雀 + 换手迟滞**：短周期普跌切债券；换手不足阈值不调仓。迟滞在周循环内生效，**周度断路器 / 杠杆开关跟踪的是迟滞后实盘权重**（非未执行的原始提议权重）。

## 回测结果（冻结参数，cost=2bp，borrow=2%，2020-09 → 2026-08）

硬交付目标：**Sharpe > 2.3 / 年化 > 25% / MDD ≤ 8%**

- 年化收益 **~25.05%**
- Sharpe（rf=0）**~2.417**
- 最大回撤 **~-6.93%**
- 目标检查：**三项硬目标全部达成**

分年收益（约）：

| 年 | 收益 |
|---|---:|
| 2020 | +3.7% |
| 2021 | -0.5% |
| 2022 | +3.3% |
| 2023 | +44.7% |
| 2024 | +45.4% |
| 2025 | +35.9% |
| 2026YTD | +23.1% |

> 注：2021 年微幅为负，是为压回撤/抬升 Sharpe 与年化所付出的代价；若强制“全年非负”，硬目标组合目前不可同时满足。

> 汇丰控股为港股通标的（00005.HK），行情按港币计、映射到 A 股交易日历；实盘需考虑汇率与港股通额度/溢折价。

> 条件杠杆涉及融资成本假设（默认年化 2%）；实盘杠杆能力、保证金与 QDII 溢折价需单独评估。历史回测不代表未来收益。

## Web 控制台

```bash
cd multi_asset_rotation
pip install -r requirements.txt
python web_app.py                  # 默认 http://0.0.0.0:8080
python web_app.py --port 8080
```

功能页：

1. `/research` 策略可行性研究报告（逻辑 / 决策方法 / 风控细则）
2. `/monitor` 指标监控与调仓建议（净值、金丝雀、差额指令）
3. `/forecast` 本周期持仓收益率预计（动量/历史外推与区间）

## 快速运行（命令行回测）

```bash
cd multi_asset_rotation
pip install -r requirements.txt
python run.py                 # 使用缓存/自动下载
python run.py --force-download  # 强制重拉 akshare 数据
```

输出目录：`multi_asset_rotation/output/`

- `nav_curve.png` / `drawdown.png` / `weights.png` / `attribution.png`
- `yearly_returns_bar.png`
- `asset_yearly_compare.png` / `asset_yearly_heatmap.png`
- `latest_signal.json` / `latest_orders.csv`
- `REPORT.md` / `summary.json` / `final_weight_params.json`
- `trades.csv` / `weights_signal_friday.csv`

## 数据说明

- 数据源：`akshare`（优先 `stock_zh_a_hist_tx` 前复权，失败回退 `fund_etf_hist_sina`）
- 缓存：`multi_asset_rotation/data/{code}.csv`

## 冻结参数

见 `config.py` 中 `PARAMS`（摘要）：

```python
mom_lb=8, abs_lb=4, sma_lb=35, sma_buffer=0.01, require_abs_pos=True,
vol_target=0.14, top_k=3, max_single=0.50, canary_k=4, rebalance_thresh=0.10,
max_gross=1.5, boost_mom=0.03, boost_min_n=1, lev_dd_cap=0.03,
dd_stop=0.06, dd_resume=0.02,
daily_dd_stop=0.05, stop_only_levered=True, stop_vol_mult=1.5,
dd_action="bonds", resume_on_rebalance=True,
borrow_rate=0.02, cost_bps=2.0
```

## 实盘执行清单

1. 每周五收盘后运行 `python run.py`，读取最新信号权重；
2. 下周一开盘按目标权重做**差额调仓**（注意杠杆仓位的融资/融券约束）；
3. QDII（美股/黄金）建议限价单；
4. 若组合盘中回撤触发日度止损规则，按风控切债券，等待下次信号日再平衡。

## 每日微信推送（云端定时 · 约 19:00）

程序：`daily_notify.py`  

为与回测一致（**周五收盘出信号 → 下周一执行**），并避免周五 19:00 港股/美股行情未齐误推：

| 时间 | 推送内容 |
|---|---|
| **周一～周五 19:00** | 今日盈亏、本周至今盈亏、本周已生效持仓、YTD **累计收益率折线图** |
| **周六 19:00** | 在以上基础上，**加推**红色表头「下周一策略调仓目标建议！」 |

- **数据齐全校验（双保险）**：周六若港股/美股周五原始收盘仍未入库，目标会强制沿用上周持仓并醒目提示（2026-08-21 事故复盘）

> 重要：微信**不能**只凭手机号直发。需要你用微信扫码绑定推送平台拿到 `token`，再由云端调用 API 推到你的微信。手机号只用于消息抬头展示（或 PushPlus 短信渠道）。

### 1) 获取推送 Token（推荐 PushPlus）

1. 打开 [pushplus.plus](https://www.pushplus.plus/)  
2. 微信扫码关注并登录，完成实名  
3. 复制个人 `token`  
4. （可选）在个人中心绑定手机号 `15111101843`，若要用短信渠道

也可改用 Server酱 `SendKey`，或企业微信群机器人 Webhook。

### 2) 本地试跑

```bash
cd multi_asset_rotation
cp .env.example .env
# 编辑 .env：填入 PUSHPLUS_TOKEN，确认 WECHAT_PHONE
python daily_notify.py --dry-run                              # 按今天星期几生成
REPORT_DAY=2026-08-21 python daily_notify.py --dry-run        # 测周五：应无调仓红条
REPORT_DAY=2026-08-22 python daily_notify.py --dry-run        # 测周六：应有下周一调仓建议
python daily_notify.py                                        # 真正推送
```

报告落盘：`output/daily_notify_latest.txt|html|json`

### 3) GitHub Actions 云端定时（周一至周六 19:00，含备份）

仓库已含工作流：`.github/workflows/daily_strategy_notify.yml`

- 主定时：北京 **19:00**（UTC 11:00）
- 备份定时：北京 **19:20 / 20:00**（防 GitHub cron 漏跑）
- 同一天若已成功推送，备份时段会自动跳过，避免重复消息

在 GitHub → Settings → Secrets and variables → Actions 添加：

| Secret | 说明 |
|---|---|
| `PUSHPLUS_TOKEN` | **必填**（推荐） |
| `WECHAT_PHONE` | 可选，默认示例手机号仅作抬头 |
| `SERVERCHAN_SENDKEY` | 可选 |
| `WECOM_WEBHOOK` | 可选 |
| `PUSHPLUS_CHANNEL` | 可选，默认 `wechat` |

然后在 Actions 里手动 Run workflow 测试一次。

### 4) 云主机 cron

```bash
# crontab -e（机器时区设为 Asia/Shanghai）
0 19 * * 1-6 cd /path/to/multi_asset_rotation && bash scripts/run_daily_notify.sh >> output/daily_notify_cron.log 2>&1
```
