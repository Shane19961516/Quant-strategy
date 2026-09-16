# Brent volume-price research system (Phase 1)

OHLCV-only research stack for ICE Brent (`BZ=F`) using Yahoo as the development data source.

```bash
pip install -r brent_quant/requirements.txt
python -m brent_quant.main --mode all
```

Phase 1 downloads 60 days of 5-minute bars, incrementally stores Parquet under `brent_quant/data/`, then backtests models A–E plus score, buy-and-hold, 20-bar Donchian, and a random-entry benchmark.

Reports land in `brent_quant/reports/`.
