# Strategy Audit & Remediation

## Critical issues found in v1
1. `require_spy_pos` used *same-week* SPY return → look-ahead bias (Sharpe inflated ~3.08 → ~2.3 when removed).
2. Regime MA / momentum confirm used week-t close for week-t PnL without lag.
3. Yahoo `info` fundamentals broadcast as constant panels → valuation/profitability/quality look-ahead.
4. MaxDD tuned exactly to -10% with fragile brakes; late-sample equity explosion suggested overfit overlays.

## Remediation in causal_v2
- Strict `shift(1)` on all market overlays.
- Production factor set = momentum + stability + size (price-based).
- Walk-forward IS/OOS split at 2021-12-31 reported in SUMMARY.
- Re-optimized under causal constraints.

## Delivery status: targets_hit=False