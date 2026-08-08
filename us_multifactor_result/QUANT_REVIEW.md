# Quant Review & Optimization Notes

## What works
- Cross-sectional price factors (short-term reversal + intermediate momentum mix after IC sign alignment, size, and vol-related signals) with weekly Top-N.
- Simple equal-weight + lagged drawdown brake — robust, low parameter fragility.
- Walk-forward OOS Sharpe ≥ IS Sharpe on the primary book (good generalization signal).

## What does not work (under causal constraints)
- Same-week SPY filters (look-ahead).
- Yahoo snapshot fundamentals as historical panels.
- Intra-week daily stops on this book (destroyed OOS Sharpe in tests).
- Heavy regime gating to force Sharpe→3 (kills CAGR; does not reach 3 causally).
- Long/short dollar-neutral 2× on this universe (high MDD, Sharpe <1).

## Recommended next upgrades (research backlog)
1. Point-in-time fundamentals (Compustat/FactSet) for true quality/value sleeves.
2. Point-in-time S&P500 membership (survivorship-safe universe).
3. Sector-neutral residualization of scores.
4. Expanding-window ICIR factor weights (no full-sample freeze).
5. If MDD≤10% is hard mandate: use defensive vol-target book and accept CAGR~20%.
6. If Sharpe≥3 is hard mandate: need different product (intraday, options overlay, or much higher leverage on a market-neutral book with capacity limits).

## Capacity / implementation
- Weekly Friday close, Top-15 EW, ~30% avg turnover → 10bp cost assumption is conservative for liquid SPX names.
- Avg exposure ~0.6 under DD brake — report both gross and capital-adjusted metrics to stakeholders.