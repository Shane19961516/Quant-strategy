# Brent 原油纯量价研究系统（第一阶段）

以 ICE Brent（Yahoo 代码 `BZ=F`）为标的、只用 OHLCV 的研究与回测栈。开发数据源为 Yahoo Finance。

```bash
pip install -r brent_quant/requirements.txt
python -m brent_quant.main --mode all
python -m brent_quant.main --mode backtest --ticker GC=F --reports-dir brent_quant/reports/gold
python -m brent_quant.main --mode backtest --ticker SI=F --reports-dir brent_quant/reports/silver
python -m universal_quant.main
```

第一阶段下载最近 60 天的 5 分钟 K 线，增量写入 `brent_quant/data/` 下的 Parquet，然后回测模型 A–E、综合 SCORE、买入持有、20 根 Donchian，以及随机入场基准。默认标的是 Brent `BZ=F`；`GC=F` / `SI=F` 使用 COMEX 黄金、白银的乘数与 tick。

报告输出到 `brent_quant/reports/`。
