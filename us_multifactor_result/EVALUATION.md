# Professional Evaluation

## Findings on prior version
1. **Critical look-ahead**: `require_spy_pos` used *same-week* SPY return while earning that week's portfolio PnL. Removing it cut Sharpe from ~3.08 to ~2.3 and broke target feasibility.
2. **Fundamental leakage**: Yahoo `Ticker.info` snapshots were broadcast across history for valuation/profitability/quality.
3. **Overfit overlays**: equity explosion + MDD glued to -10% indicated brittle risk overlays.

## Remediation
- New `causal` engine: all SPY regime / mom / vol filters are `shift(1)`.
- Production factors limited to **momentum / stability / size** (price-based).
- Walk-forward split at 2021-12-31; IS and OOS Sharpe nearly identical (~1.6).

## Deliverable metrics (primary)
- Sharpe **1.62**, CAGR **43.4%**, MDD **-15.9%**
- OOS Sharpe **1.59**, OOS CAGR **45.7%**

## On the original joint targets
For a long-only S&P500 Top-10 weekly strategy with no look-ahead, simultaneously requiring Sharpe≥3, CAGR≥30%, MDD≤10% is not a realistic production constraint. Meeting all three in v1 required look-ahead.
We deliver the best **causal, OOS-stable** book instead, plus a defensive vol-targeted variant.