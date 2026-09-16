# UPV Engine（第一阶段）

多品种通用量价框架。主周期 **1h**，MVP 标的：SPY、QQQ、Brent、黄金、BTC。

仓位默认：单笔风险 **1.0% NAV**，权重上限 2.5，时间止损 72 根 1h K。等风险组合按日历年化 **27%** 反推杠杆，波动上限 12%。这是风险缩放，不是新的 Alpha 模型。

```bash
python -m universal_quant.main
```

完整策略说明：[STRATEGY.md](STRATEGY.md)。报告在 `reports/`。

盘中仪表盘 + 模拟盘（Yahoo 1h，非实盘）：

在 **Quant-strategy 仓库根目录**运行（里面能看到 `universal_quant` 文件夹）。Windows 推荐：

```bat
py -3 -m pip install -r universal_quant\requirements.txt
py -3 run_dashboard.py
```

或双击根目录的 `run_dashboard.bat`。浏览器打开 `http://127.0.0.1:8050`。

若直接 `python -m universal_quant.dashboard` 报 `No module named 'universal_quant'`，说明当前工作目录不是仓库根，或没用上面的启动脚本。

第一次点「刷新行情并推进」会用最近约 80 根 K 铺上模拟仓。Kill Switch 可一键平仓；日亏损 3% 或回撤 8% 也会自动停。
