# Strategy Audit & Remediation

## Critical issues found in v1
1. `require_spy_pos` used *same-week* SPY return → look-ahead bias.
2. Regime / momentum confirm used week-t close for week-t PnL without lag.
3. Yahoo `info` fundamentals broadcast as constant panels.

## Remediation timeline
- **causal_v2**: strict `shift(1)` overlays; price factors only; Top-10.
- **causal_v3**: Top-15 + stability tilt + tighter DD brake; frozen factors; IS IC API.

## Delivery status: soft_hit=True joint_hit=False