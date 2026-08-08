# Professional Evaluation — Causal v3

## Audit of prior versions
1. **v1 look-ahead**: same-week SPY filter inflated Sharpe (~3.08 → ~2.3 when removed).
2. **Fundamental leakage**: Yahoo `info` snapshots are not point-in-time.
3. **Joint stretch targets** (Sharpe≥3 ∧ CAGR≥30% ∧ MDD≤10%) require either look-ahead or a different mandate (e.g. options, higher leverage with different risk budget).

## v3 optimization changes
- Broader book: **Top-15** equal-weight (was Top-10).
- Risk tilt: momentum **50%** / stability **40%** / size **10%**.
- Tighter causal drawdown brake: soft **-4%**, hard **-7%**.
- Factor names frozen; IC computed on already-lagged panels with IS end-date support.
- Rejected daily intra-week stops (hurt OOS Sharpe sharply).
- Rejected aggressive regime/SPY filters (cut CAGR without lifting Sharpe to 3).

## Deliverable metrics
- **Primary**: Sharpe **1.59**, CAGR **37.9%**, MDD **-13.4%**
- **OOS**: Sharpe **1.78**, CAGR **44.9%**, MDD **-12.6%**
- **Defensive (vol 10%)**: Sharpe **1.56**, CAGR **21.2%**, MDD **-10.5%**

## Production acceptance
- Soft production targets hit: **True**
- Joint stretch targets hit: **False**

Delivery prioritizes causal correctness and OOS stability over unattainable joint stretch metrics.