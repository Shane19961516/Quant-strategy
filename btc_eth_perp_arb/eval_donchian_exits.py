"""Train-only Donchian TP/SL economics. Not a new book; OOS is not scored."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import (
    DONCHIAN_BAR_MINUTES,
    DONCHIAN_EQUITY,
    DONCHIAN_FRACTION,
    DONCHIAN_LEVERAGE,
    DONCHIAN_TP_MULTIPLE,
    DONCHIAN_WINDOW,
    donchian_config,
)
from .data import load_panel, resample_panel
from .donchian_exits import (
    ATR14,
    ATR144,
    DAILY_ATR_N,
    TP_MARGIN_MULTS,
    ExitStudySpec,
    breakout_excursions,
    prepare_exit_panel,
    summarize_excursions,
)
from .eval_entry import TRAIN_END, TRAIN_START, _end_ts, _ts
from .plots import plot_mae_mfe_scatter, plot_tp_hit_bars

CACHE_LONG = "aligned_1m_long.parquet"
SYMBOLS = ("BTCUSDT", "ETHUSDT")


def evaluate(panel) -> dict:
    cfg = donchian_config()
    if cfg.bar_minutes > 1:
        print(f"resample 1m → {cfg.bar_minutes}m rows={len(panel)}", flush=True)
        panel = resample_panel(panel, cfg.bar_minutes)
        print(f"resampled rows={len(panel)}", flush=True)
    signaled = prepare_exit_panel(panel, cfg)
    train_start = _ts(TRAIN_START)
    train_end = _end_ts(TRAIN_END)
    out = {
        "step": "donchian_train_exits",
        "not_a_book": True,
        "oos_used": False,
        "last_month_used": False,
        "shell": {
            "bar_minutes": DONCHIAN_BAR_MINUTES,
            "window": DONCHIAN_WINDOW,
            "fraction": DONCHIAN_FRACTION,
            "leverage": DONCHIAN_LEVERAGE,
            "frozen_tp_multiple": DONCHIAN_TP_MULTIPLE,
            "starting_equity": DONCHIAN_EQUITY,
            "atr14": ATR14,
            "atr144": ATR144,
            "daily_atr": DAILY_ATR_N,
            "tp_margin_mults": list(TP_MARGIN_MULTS),
            "literature": {
                "fixed_take_profit": False,
                "stop": "2N ≈ 2% equity (Original Turtle Rules)",
                "winning_exit": "opposite Donchian of half the entry window (72 × 5m)",
            },
        },
        "train": [TRAIN_START.isoformat(), TRAIN_END.isoformat()],
        "modes": {},
        "trades": {},
    }
    for mode in ("independent", "sequential"):
        out["modes"][mode] = {}
        for symbol in SYMBOLS:
            print(f"excursions {symbol} mode={mode}", flush=True)
            spec = ExitStudySpec(
                train_start_ts=train_start,
                train_end_ts=train_end,
                mode=mode,
            )
            trades = breakout_excursions(signaled, symbol=symbol, cfg=cfg, spec=spec)
            summary = summarize_excursions(trades)
            out["modes"][mode][symbol] = summary
            out["trades"][f"{symbol}_{mode}"] = trades
            print(
                json.dumps(
                    {
                        "symbol": symbol,
                        "mode": mode,
                        "n": summary.get("n"),
                        "mfe_margin_p50": (summary.get("mfe_margin_mult") or {}).get("p50"),
                        "mae_price_p50": (summary.get("mae_price_pct") or {}).get("p50"),
                        "atr_daily_p50": (summary.get("atr_daily_pct") or {}).get("p50"),
                        "two_n_daily_p50": (summary.get("two_n_daily_pct") or {}).get("p50"),
                        "mfe_reaches_5x": summary.get("mfe_reaches_5x_share"),
                        "mfe_reaches_1x": summary.get("mfe_reaches_1x_share"),
                    },
                    indent=2,
                    default=str,
                ),
                flush=True,
            )
    return out


def _pct(x) -> str:
    if x is None:
        return "—"
    return f"{100.0 * float(x):.2f}%"


def _n(x) -> str:
    if x is None:
        return "—"
    return f"{float(x):.3f}"


def render_report(eval_out: dict, charts: dict[str, str]) -> str:
    ind = eval_out["modes"]["independent"]
    seq = eval_out["modes"]["sequential"]

    def row(sym: str, pack: dict, mode: str) -> str:
        mfe = pack.get("mfe_margin_mult") or {}
        mae = pack.get("mae_price_pct") or {}
        n2 = pack.get("two_n_daily_pct") or {}
        ch = pack.get("channel_width_pct") or {}
        m72 = pack.get("move_at_72_margin_mult") or {}
        return (
            f"| {sym} {mode} | {pack.get('n', 0)} | {_n(mfe.get('p50'))} | {_n(mfe.get('p90'))} "
            f"| {_pct(mae.get('p50'))} | {_pct(n2.get('p50'))} | {_pct(ch.get('p50'))} "
            f"| {_pct(pack.get('mfe_reaches_1x_share'))} | {_pct(pack.get('mfe_reaches_5x_share'))} "
            f"| {_n(m72.get('p50'))} |"
        )

    lines = [
        "| 样本 | n | MFE 投入倍数 p50 | p90 | MAE 价格 p50 | 2N(日) p50 | 通道宽 p50 | 摸到 1×投入 | 摸到 5×投入 | 72 根出场投入倍数 p50 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        row("BTC", ind["BTCUSDT"], "独立突破"),
        row("ETH", ind["ETHUSDT"], "独立突破"),
        row("BTC", seq["BTCUSDT"], "冻结连仓"),
        row("ETH", seq["ETHUSDT"], "冻结连仓"),
    ]
    hit_lines = [
        "| 止盈倍数 | BTC 先摸到止盈 | ETH 先摸到止盈 |",
        "|---:|---:|---:|",
    ]
    btc_hit = (ind["BTCUSDT"].get("hit_rates") or {}).get("2pct_equity") or {}
    eth_hit = (ind["ETHUSDT"].get("hit_rates") or {}).get("2pct_equity") or {}
    for m in ("0.2x", "0.5x", "1x", "2x", "3x", "5x"):
        hit_lines.append(
            f"| {m} | {_pct((btc_hit.get(m) or {}).get('tp_before_stop'))} "
            f"| {_pct((eth_hit.get(m) or {}).get('tp_before_stop'))} |"
        )
    chart_lines = "\n".join(f"- `{p}`" for p in charts.values()) if charts else "(none)"
    btc_n2 = (ind["BTCUSDT"].get("two_n_daily_pct") or {}).get("p50")
    eth_n2 = (ind["ETHUSDT"].get("two_n_daily_pct") or {}).get("p50")
    btc_atr = (ind["BTCUSDT"].get("atr_daily_pct") or {}).get("p50")
    eth_atr = (ind["ETHUSDT"].get("atr_daily_pct") or {}).get("p50")
    return f"""# 唐奇安 144×5m：止盈倍数与止损（仅训练段）

检索 + 冻结 10x 突破在 **2025-10-01→2026-06-30** 的 MAE/MFE。不是新书，不在 OOS / 近一月上搜止盈止损。

文献（Original Turtle Rules）：**没有固定止盈**；赢的仓用更短的反向唐奇安出场（这里对应 72×5m）；**一定要有预先写死的止损**，1N≈权益 1%，止损 2N≈权益 2%。海龟原话：不止损的交易者长期会破产。

本外壳几何（1/10 逐仓 × 10x，名义≈本金）：

| 概念 | 价格 | 投入倍数 |
|---|---|---|
| 海龟 2% 权益止损 | 2% | 0.2× |
| 日 ATR(20) 的 2N（训练中位） | BTC {_pct(btc_n2)} / ETH {_pct(eth_n2)} | ×10 |
| 逐仓强平 | ≈9.6% | 0.96× |
| 现在的 5×投入止盈 | 50% | 5× |

日 ATR 中位 BTC {_pct(btc_atr)}、ETH {_pct(eth_atr)}。5 分钟 ATR(14) 的 2N 只有几个 bp，对 12h 通道是噪声止损，不用。

独立突破 = 每次通道外逸的第一根信号，持有到强平或训练结束（忽略 5×止盈）。冻结连仓 = 与现回测一样空仓才开，所以笔数少、持有长。

## 训练段路径

{chr(10).join(lines)}

## 若加 2% 权益止损，各止盈倍数有多少笔会先摸到（同根 K 算止损先）

{chr(10).join(hit_lines)}

## 结论（先验，不是 OOS 选参）

1. **需要止损。** 现书把强平当止损，10x 逐仓大约逆向 9.6% 才走，训练里独立突破的 MAE 中位就贴在这条线上。合理先验是海龟 **2% 权益 ≈ 2% 价格 ≈ 0.2×投入**，必须严于 9.6%，否则止损就是强平。日线 2N 中位 BTC {_pct(btc_n2)}（多数在强平之内），ETH {_pct(eth_n2)} **宽于 9.6%**，所以 ETH 不能直接用裸的日线 2N 当逐仓止损，应与 2% 权益止损取紧的那个，或把 2N 封顶在约 8% 价格。
2. **5×投入不是合理止盈。** 那是 50% 价格、相对 2% 止损是 25R。12h 通道上训练独立突破很少把 MFE 推到 5×；海龟本身也没有固定止盈。
3. **若必须写一个固定止盈倍数：** 不要高于 **1×投入（10% 价格，约 5R vs 2% 止损）**；更干净的是 **0** 固定止盈，改用 72 根反向通道作为赢的出场。72 根出场的已实现投入倍数看上表。
4. 不要加杠杆来让 5×止盈“变得容易”。50x 已经在训练上丢掉。
5. 上表不是新交付书。要改出场，下一刀只锁一个旋钮，仍只在训练上弃留。

## 图表

{chart_lines}

```bash
python -m btc_eth_perp_arb.eval_donchian_exits
python -m pytest btc_eth_perp_arb/tests -q
```
"""


def write_charts(eval_out: dict, media: Path) -> dict[str, str]:
    media.mkdir(parents=True, exist_ok=True)
    trades = eval_out["trades"]
    ind = {
        "BTCUSDT": trades["BTCUSDT_independent"],
        "ETHUSDT": trades["ETHUSDT_independent"],
    }
    paths = {}
    p = media / "donchian-exits-mae-mfe.png"
    plot_mae_mfe_scatter(ind, p)
    paths["mae_mfe"] = str(p)
    hit = {
        "BTCUSDT": eval_out["modes"]["independent"]["BTCUSDT"].get("hit_rates") or {},
        "ETHUSDT": eval_out["modes"]["independent"]["ETHUSDT"].get("hit_rates") or {},
    }
    p = media / "donchian-exits-tp-hits.png"
    plot_tp_hit_bars(hit, p)
    paths["tp_hits"] = str(p)
    return paths


def jsonable(eval_out: dict) -> dict:
    payload = {k: v for k, v in eval_out.items() if k != "trades"}
    trades = {}
    for name, frame in eval_out.get("trades", {}).items():
        if frame is None or getattr(frame, "empty", True):
            trades[name] = []
        else:
            trades[name] = json.loads(frame.to_json(orient="records"))
    payload["trade_counts"] = {k: len(v) for k, v in trades.items()}
    return payload


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--media-dir", default="")
    p.add_argument("--summary-json", default="")
    p.add_argument("--report-md", default="")
    args = p.parse_args(argv)
    panel, _ = load_panel(name=CACHE_LONG)
    # Warmup before train is kept; bars after train end are not scored.
    panel = panel[panel["bar_open_ts"] <= _end_ts(TRAIN_END)].reset_index(drop=True)
    eval_out = evaluate(panel)
    paths = {}
    if args.media_dir:
        paths = write_charts(eval_out, Path(args.media_dir))
        eval_out["charts"] = paths
        print("charts", json.dumps(paths, indent=2))
        for path in paths.values():
            if not Path(path).exists():
                raise FileNotFoundError(path)
    payload = jsonable(eval_out)
    if args.summary_json:
        out = Path(args.summary_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        print("wrote", out)
    if args.report_md:
        md = render_report(eval_out, paths)
        out = Path(args.report_md)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")
        print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
