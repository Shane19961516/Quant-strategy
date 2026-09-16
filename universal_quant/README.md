# UPV Engine（第一阶段）

多品种通用量价框架。主周期 **1h**，MVP 标的：SPY、QQQ、Brent、黄金、BTC。

仓位默认：单笔风险 **1.0% NAV**，权重上限 2.5，时间止损 72 根 1h K。等风险组合按日历年化 **27%** 反推杠杆，波动上限 12%。这是风险缩放，不是新的 Alpha 模型。

```bash
python -m universal_quant.main
```

完整策略说明（数据、清洗、信号、风控、仓位）：[STRATEGY.md](STRATEGY.md)。报告在 `universal_quant/reports/`。
