"""CLI: fetch Binance UM 1m data, run the last-month residual-z backtest, write artifacts."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .config import CACHE_DIR, LEVERAGE, STARTING_EQUITY, TAKER_FEE_BPS, VENUE_NAME, BacktestConfig, delivery_config
from .data import build_aligned_panel, latest_vision_day, load_panel, save_panel
from .metrics import summarize
from .plots import plot_attribution, plot_drawdown, plot_equity, plot_z_and_pos
from .signals import add_signals
from .simulator import run_simulator


def _parse_day(s: str) -> date:
    return date.fromisoformat(s)


def render_report(
    summary: dict,
    manifest: dict,
    cfg: BacktestConfig,
    media: dict[str, str],
    extra: str = "",
) -> str:
    eq_ret = summary["equity_return"]
    mdd = summary["max_drawdown"]
    funding = summary["funding_sum"]
    fees = summary["fee_sum"]
    trades = summary["trades"]
    liq = summary["liquidation_events"]
    notional = summary["notional_mark_pnl"]

    tradable = (
        eq_ret > 0
        and liq == 0
        and summary["funding_cash_off_settlement_abs"] == 0
        and False  # one month never clears Gate C by itself
    )
    gate_c = "not passed"
    why = []
    if eq_ret <= 0:
        why.append(
            f"net equity return after fees+funding is {eq_ret:.2%} on this window — no economic edge after costs"
        )
    if liq > 0:
        why.append(f"{liq} liquidation event(s) under 10x cross — path risk is real")
    if abs(fees) >= abs(summary["spread_like_pnl"]) and summary["spread_like_pnl"] > 0:
        why.append("fees consume the spread PnL (or more)")
    if abs(fees) > abs(summary["spread_like_pnl"]):
        why.append(
            f"fee drag {fees:.2f} USDT vs spread/mark/slippage {summary['spread_like_pnl']:.2f} USDT"
        )
    why.append(
        "this is a single recent ~1-month slice, not a locked train/validate/test split or walk-forward; "
        "Gate A (IC/OOS) and Gate C (feasibility) are **not** fully passed by this window alone"
    )
    if tradable:
        verdict = "would still not be a Gate C pass"
    else:
        verdict = "not tradable at 10x on this evidence (Gate C fail / incomplete)"

    media_lines = "\n".join(f"- `{p}`" for p in media.values())
    fs = manifest.get("funding_stats") or {}
    fs_txt = json.dumps(fs, indent=2)

    return f"""# BTC/ETH 永续相对价值 — 近一月回测

本文件是研究产物（不进 git）。策略代码在仓库 `btc_eth_perp_arb/`。

## 数据契约（已落地）

| 项 | 取值 |
|---|---|
| 交易所 / 品种 | {VENUE_NAME} 永续 `{manifest.get("symbols")}` |
| 周期 | 1 分钟，左闭右开 `[t, t+60s)` |
| 拉取方式 | `{manifest.get("source")}` 日频 zip（`fapi.binance.com` 在本环境 HTTP 451 地理限制，未混用 OKX 价格） |
| last OHLCV | `daily/klines`（成交价，只用于执行） |
| mark OHLC | `daily/markPriceKlines`（PnL / 保证金 / 强平） |
| index | `daily/indexPriceKlines` close |
| funding | 官方 `monthly/fundingRate`；尚未公布的月份用 1m premium index + Binance clamp 公式重建，并与上一完整月官方序列对账 |
| 盘口 | Vision 无历史 BBO；执行用假定半价差 BTC 0.5bp / ETH 1.0bp + 线性冲击，**taker {cfg.taker_fee_bps:.1f}bp** |
| 双腿对齐 | 完整 UTC 分钟日历 left-join 两腿；缺腿标记 `pair_incomplete=1`，**不对 close/mark 做前向填充** |
| 前视 | bar t 的 z/β 只在 **bar t+1 open** 成交 |
| 资金费 | 仅结算分钟入账，未摊到 480 根 bar |
| 样本窗 | fetch `{manifest.get("fetch_start")}` → `{manifest.get("end")}`；**PnL 只计** `{summary["pnl_start"]}` → `{summary["pnl_end"]}` UTC |

Funding reconstruction vs official (overlap month):

```json
{fs_txt}
```

## 策略（与 research-workflow 主路径一致，未在本窗上搜参）

- 因子：`z = (log(ETH_mark/BTC_mark) − μ_{{t-1}}) / σ_{{t-1}}`，窗口 {cfg.z_window} 分钟。
- 对冲：ETH 对 BTC 的 mark 收益滚动 β（{cfg.beta_window} 分钟，已 `shift(1)`），入场时冻结，不金字塔加仓。
- 入场 `|z|≥{cfg.entry_z}`，平仓 `|z|≤{cfg.exit_z}`，止损 `|z|≥{cfg.stop_z}` 或持仓 {cfg.max_hold_bars} 分钟或 60 分钟相关 `< {cfg.corr_min}`。
- 全仓交叉保证金，目标毛名义 / 权益 = **{cfg.leverage:.0f}x**；维持保证金率 0.4%。
- 结算前后 ±2 分钟禁止新开；连续缺 bar > 2 分钟冻结新开并在下一根完整 bar 平仓。

这是 **最近一个月的 OOS 风格切片**，参数来自流程文档默认值，不是在本月上网格搜出来的。它 **不能** 单独通过 Gate A（无独立验证/测试段 IC）或 Gate C（无 walk-forward、无容量曲线、无多 regime）。

## 结果摘要（10x 权益账，扣手续费 + funding）

| 指标 | 数值 |
|---|---|
| 实际 PnL 区间 | `{summary["pnl_start"]}` → `{summary["pnl_end"]}` |
| 分钟数 / 完整双腿分钟 | {summary["minutes"]:,} / {summary["complete_minutes"]:,} |
| 缺腿分钟（未 ffill） | {summary["incomplete_minutes"]:,} |
| 起始权益 | {summary["starting_equity"]:,.2f} USDT |
| 期末权益 | {summary["ending_equity"]:,.2f} USDT |
| **权益 PnL / 收益率** | **{summary["equity_pnl"]:,.2f} USDT / {eq_ret:.2%}** |
| 名义 mark PnL（滞后仓位 × Δmark） | {notional:,.2f} USDT |
| 相对 10x 毛名义的名义收益率 | {summary["notional_return_on_gross_10x"]:.2%} |
| 最大回撤（权益） | {mdd:.2%} |
| 交易次数（双腿开仓事件） | {trades} |
| 成交笔数（单腿 fill） | {summary["fills"]} |
| 手续费 | {fees:,.2f} USDT |
| Funding 现金流 | {funding:,.2f} USDT |
| 价差/滑点/mark 项 | {summary["spread_like_pnl"]:,.2f} USDT |
| 强平次数 | {liq} |
| 在市时间 | {summary["time_in_market"]:.1%} |
| 结算分钟数 / 非结算分钟 funding 绝对值 | {summary["funding_settlements_in_window"]} / {summary["funding_cash_off_settlement_abs"]:.4f} |
| 在市平均毛杠杆 | {summary["mean_gross_leverage_in_pos"]:.2f}x |
| 出场原因 | `{summary["exit_reasons"]}` |

Δequity ≈ 价差/mark/滑点 + funding − fees。Funding 只在结算分钟非零（非结算分钟绝对值 {summary["funding_cash_off_settlement_abs"]:.4f}）。

## Gate C — 是否可交易

**判定：{verdict}（{gate_c}）。**

{chr(10).join("- " + x for x in why)}

1 分钟 taker {cfg.taker_fee_bps:.1f}bp × 两腿开平，10x 毛名义下每轮换手对权益的成本量级是几十个 bp 到 1%，均值回复必须非常强才能覆盖。本窗是短样本：即使数字为正，也不能当成可上 10x 真金的证据。

## 图表

{media_lines}

## 复现

```bash
python -m btc_eth_perp_arb.run --refresh
```

缓存：仓库内 `btc_eth_perp_arb/cache/aligned_1m.parquet` + `manifest.json`。
{extra}
"""


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start", default="", help="PnL window start YYYY-MM-DD (UTC)")
    p.add_argument("--end", default="", help="last complete Vision day; default=latest available")
    p.add_argument("--warmup-days", type=int, default=5)
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--leverage", type=float, default=None)
    p.add_argument("--equity", type=float, default=STARTING_EQUITY)
    p.add_argument("--fee-bps", type=float, default=TAKER_FEE_BPS)
    p.add_argument("--adv-participation", type=float, default=None, help="max fraction of bar quote volume per leg (default from config)")
    p.add_argument("--invert", action="store_true", help="flip spread side; keep |z| thresholds")
    p.add_argument("--preset", choices=["default", "delivery"], default="default")
    p.add_argument("--long-cache", action="store_true", help="use aligned_1m_long.parquet if present")
    p.add_argument("--report-md", default="")
    p.add_argument("--media-dir", default="")
    p.add_argument("--summary-json", default="")
    args = p.parse_args(argv)

    end = _parse_day(args.end) if args.end else latest_vision_day()
    if args.start:
        start = _parse_day(args.start)
    else:
        start = end - timedelta(days=29)

    cache_name = "aligned_1m_long.parquet" if args.long_cache else "aligned_1m.parquet"
    cache_parquet = CACHE_DIR / cache_name
    if args.refresh or not cache_parquet.exists():
        panel, manifest = build_aligned_panel(start, end, warmup_days=args.warmup_days)
        save_panel(panel, manifest, name=cache_name)
    else:
        panel, manifest = load_panel(name=cache_name)
        print(f"Loaded cache {cache_parquet} rows={len(panel)}")

    if args.preset == "delivery":
        kw = dict(
            invert_signal=bool(args.invert),
            taker_fee_bps=args.fee_bps,
            starting_equity=args.equity,
        )
        if args.leverage is not None:
            kw["leverage"] = args.leverage
        if args.adv_participation is not None:
            kw["adv_participation"] = float(args.adv_participation)
        cfg = delivery_config(**kw)
    else:
        cfg = BacktestConfig(
            leverage=LEVERAGE if args.leverage is None else args.leverage,
            taker_fee_bps=args.fee_bps,
            starting_equity=args.equity,
            invert_signal=bool(args.invert),
            adv_participation=float(args.adv_participation)
            if args.adv_participation is not None
            else BacktestConfig().adv_participation,
        )
    panel = add_signals(panel, cfg)
    start_ts = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp() * 1000)
    # If cache was built for a different range, clip end.
    end_ts = int(
        datetime(end.year, end.month, end.day, 23, 59, tzinfo=timezone.utc).timestamp() * 1000
    )
    panel = panel[(panel["bar_open_ts"] <= end_ts)].reset_index(drop=True)

    result = run_simulator(panel, cfg, trade_start_ts=start_ts)
    summary = summarize(result, pnl_start_ts=start_ts, starting_equity=cfg.starting_equity)
    summary["venue"] = VENUE_NAME
    summary["leverage"] = cfg.leverage
    summary["fee_bps"] = cfg.taker_fee_bps
    print(json.dumps({k: summary[k] for k in summary if k != "exit_reasons"}, indent=2, default=str))
    print("exit_reasons", summary["exit_reasons"])

    media_map: dict[str, str] = {}
    if args.media_dir:
        media = Path(args.media_dir)
        media.mkdir(parents=True, exist_ok=True)
        w = result.bars[result.bars["bar_open_ts"] >= start_ts]
        plot_equity(
            w,
            media / "backtest-last-month-equity.png",
            f"BTC–ETH RV equity {cfg.leverage:.0f}x  {summary['pnl_start'][:10]} → {summary['pnl_end'][:10]}",
        )
        plot_drawdown(w, media / "backtest-last-month-drawdown.png")
        plot_z_and_pos(w, media / "backtest-last-month-zscore.png")
        plot_attribution(w, media / "backtest-last-month-attribution.png")
        media_map = {
            "equity": str(media / "backtest-last-month-equity.png"),
            "drawdown": str(media / "backtest-last-month-drawdown.png"),
            "zscore": str(media / "backtest-last-month-zscore.png"),
            "attribution": str(media / "backtest-last-month-attribution.png"),
        }
        for path in media_map.values():
            if not Path(path).exists():
                raise FileNotFoundError(path)

    if args.summary_json:
        Path(args.summary_json).write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")

    if args.report_md:
        md = render_report(summary, manifest, cfg, media_map)
        out = Path(args.report_md)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")
        print("wrote", out)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
