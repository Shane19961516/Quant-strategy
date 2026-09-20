# BTC / ETH USDT-M 1-minute relative-value backtest

Research write-up stays in the project store, not this repo. This package only
fetches Binance USDⓈ-M history and runs the residual z-score book.

## Data

- Venue: Binance USDⓈ-M (`BTCUSDT`, `ETHUSDT` perps)
- Source: `https://data.binance.vision` daily kline / mark / index / premium zips
  (`fapi.binance.com` is geo-blocked in some environments; do not mix another
  venue's prices into the same spread).
- Cache: `btc_eth_perp_arb/cache/aligned_1m.parquet`

```bash
pip install -r btc_eth_perp_arb/requirements.txt
python -m btc_eth_perp_arb.run --refresh
python -m pytest btc_eth_perp_arb/tests -q
```
