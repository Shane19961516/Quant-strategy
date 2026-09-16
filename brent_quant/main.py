"""Phase-1 CLI: download, clean, backtest models A–E, scan, walk-forward."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from brent_quant import config as cfg
from brent_quant.backtest import buy_and_hold, random_entry_benchmark, run_backtest
from brent_quant.data_cleaner import clean_ohlcv
from brent_quant.data_loader import incremental_update, load_parquet, processed_path, save_parquet
from brent_quant.factor_analysis import run_factor_analysis
from brent_quant.metrics import (
    bootstrap_metrics,
    hour_of_day_stats,
    monte_carlo_trade_shuffle,
    summarize_result,
)
from brent_quant.report import (
    comparison_table,
    plot_drawdown,
    plot_equity,
    write_json,
    write_markdown_report,
    write_trades,
)
from brent_quant.signal import add_factors, add_score
from brent_quant.walk_forward import walk_forward


def _table_markdown(df: pd.DataFrame) -> str:
    if df.empty:
        return "_no rows_"
    cols = [str(c) for c in df.columns]
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join("---" for _ in cols) + " |"]
    for _, row in df.iterrows():
        cells = []
        for c in df.columns:
            v = row[c]
            if isinstance(v, float):
                cells.append("" if v != v else f"{v:.4f}")
            else:
                cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Brent 量价第一阶段运行器")
    p.add_argument("--mode", default="all", choices=["download", "backtest", "scan", "walk-forward", "all"])
    p.add_argument("--skip-download", action="store_true")
    p.add_argument("--ticker", default=cfg.TICKER)
    p.add_argument("--interval", default=cfg.INTERVAL)
    p.add_argument("--period", default=cfg.PERIOD)
    p.add_argument("--models", default=",".join(cfg.MODELS))
    p.add_argument("--slippage-ticks", type=float, default=cfg.SLIPPAGE_TICKS)
    p.add_argument("--reports-dir", default=str(cfg.REPORTS_DIR))
    return p.parse_args(argv)


def load_or_download(args: argparse.Namespace) -> tuple[pd.DataFrame, dict[str, Any]]:
    dest = processed_path(args.ticker, args.interval)
    if args.skip_download or args.mode == "backtest":
        df = load_parquet(dest)
        if df.empty and not args.skip_download:
            df, meta = incremental_update(args.ticker, args.period, args.interval)
            return df, meta
        return df, {"source": str(dest), "rows": int(len(df)), "skipped_download": True}
    return incremental_update(args.ticker, args.period, args.interval)


def _param_scan(df: pd.DataFrame, slippage_ticks: float) -> dict[str, Any]:
    rows = []
    for n in cfg.BREAKOUT_GRID:
        res = run_backtest(df, model="A", breakout_n=n, slippage_ticks=slippage_ticks)
        stats = summarize_result(res)
        rows.append(
            {
                "factor": "breakout_n",
                "value": n,
                "sharpe": stats.get("sharpe"),
                "calmar": stats.get("calmar"),
                "profit_factor": stats.get("profit_factor"),
                "max_drawdown": stats.get("max_drawdown"),
                "total_return": stats.get("total_return"),
                "n_trades": stats.get("n_trades"),
            }
        )
    for n in cfg.ER_GRID:
        res = run_backtest(df, model="C", er_n=n, slippage_ticks=slippage_ticks)
        stats = summarize_result(res)
        rows.append(
            {
                "factor": "er_n",
                "value": n,
                "sharpe": stats.get("sharpe"),
                "calmar": stats.get("calmar"),
                "profit_factor": stats.get("profit_factor"),
                "max_drawdown": stats.get("max_drawdown"),
                "total_return": stats.get("total_return"),
                "n_trades": stats.get("n_trades"),
            }
        )
    for ticks in cfg.SLIPPAGE_GRID:
        res = run_backtest(df, model="D", slippage_ticks=ticks)
        stats = summarize_result(res)
        rows.append(
            {
                "factor": "slippage_ticks",
                "value": ticks,
                "sharpe": stats.get("sharpe"),
                "calmar": stats.get("calmar"),
                "profit_factor": stats.get("profit_factor"),
                "max_drawdown": stats.get("max_drawdown"),
                "total_return": stats.get("total_return"),
                "n_trades": stats.get("n_trades"),
            }
        )
    for cm in cfg.COST_MULT_GRID:
        res = run_backtest(df, model="D", slippage_ticks=slippage_ticks, cost_mult=cm)
        stats = summarize_result(res)
        rows.append(
            {
                "factor": "cost_mult",
                "value": cm,
                "sharpe": stats.get("sharpe"),
                "calmar": stats.get("calmar"),
                "profit_factor": stats.get("profit_factor"),
                "max_drawdown": stats.get("max_drawdown"),
                "total_return": stats.get("total_return"),
                "n_trades": stats.get("n_trades"),
            }
        )
    return {"rows": rows}


def run(argv: list[str] | None = None) -> dict[str, Any]:
    args = parse_args(argv)
    reports = Path(args.reports_dir)
    reports.mkdir(parents=True, exist_ok=True)
    cfg.RAW_DIR.mkdir(parents=True, exist_ok=True)
    cfg.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    raw, data_meta = load_or_download(args)
    if raw.empty:
        raise RuntimeError("no OHLCV data available")
    clean, qc = clean_ohlcv(raw)
    save_parquet(clean[list(("open", "high", "low", "close", "volume"))], processed_path(args.ticker, args.interval))

    if args.mode == "download":
        payload = {"data": data_meta, "qc": qc}
        write_json(payload, reports / "download_meta.json")
        return payload

    factored = add_score(add_factors(clean))
    factor_stats = run_factor_analysis(factored)
    write_json(factor_stats, reports / "factor_analysis.json")

    models = [m.strip().upper() for m in args.models.split(",") if m.strip()]
    results = {}
    summaries = {}
    for model in models:
        res = run_backtest(clean, model=model, slippage_ticks=args.slippage_ticks)
        results[model] = res
        stats = summarize_result(res)
        if not res.trades.empty:
            stats.update(monte_carlo_trade_shuffle(res.trades, n_paths=cfg.MONTE_CARLO_PATHS, seed=cfg.RANDOM_SEED))
            stats.update(bootstrap_metrics(res.trades, res.daily_equity, n=500, seed=cfg.RANDOM_SEED))
        summaries[model] = stats
        plot_equity(res, reports / f"equity_{model}.png")
        plot_drawdown(res, reports / f"drawdown_{model}.png")
        write_trades(res, reports / f"trades_{model}.csv")
        write_json(stats, reports / f"metrics_{model}.json")
        hod = hour_of_day_stats(res)
        hod.to_csv(reports / f"hour_of_day_{model}.csv")

    bhs = buy_and_hold(clean)
    summaries["BHS"] = summarize_result(bhs)
    results["BHS"] = bhs
    plot_equity(bhs, reports / "equity_BHS.png")
    don = run_backtest(clean, model="DONCHIAN20", slippage_ticks=args.slippage_ticks)
    summaries["DONCHIAN20"] = summarize_result(don)
    results["DONCHIAN20"] = don
    template = results.get("A") or results.get("D") or next(iter(results.values()))
    rnd = random_entry_benchmark(clean, template)
    summaries["RANDOM"] = summarize_result(rnd)
    results["RANDOM"] = rnd

    table = comparison_table(summaries)
    table.to_csv(reports / "model_comparison.csv", index=False)
    write_json(summaries, reports / "all_metrics.json")

    extra: dict[str, Any] = {"factor_analysis": factor_stats}

    if args.mode in {"scan", "all"}:
        scan = _param_scan(clean, args.slippage_ticks)
        extra["parameter_scan"] = scan
        pd.DataFrame(scan["rows"]).to_csv(reports / "parameter_scan.csv", index=False)

    if args.mode in {"walk-forward", "all"}:
        wf = walk_forward(clean, model="D", train_days=20, test_days=8, step_days=8)
        extra["walk_forward"] = {k: v for k, v in wf.items() if k != "oos_equity"}
        write_json(extra["walk_forward"], reports / "walk_forward.json")
        if wf.get("oos_equity") is not None and len(wf["oos_equity"]):
            wf["oos_equity"].to_csv(reports / "walk_forward_oos_equity.csv", header=["equity"])

    # Patch markdown table without tabulate.
    from brent_quant import report as report_mod

    original = report_mod.write_markdown_report

    def _write_md(path, **kwargs):
        text_table = _table_markdown(comparison_table(kwargs["summaries"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        body = [
            "# Brent 量价第一阶段回测报告",
            "",
            "## 数据",
            "",
            "```json",
            json.dumps(data_meta, indent=2, default=str),
            "```",
            "",
            "## 数据质检",
            "",
            "```json",
            json.dumps(qc, indent=2, default=str),
            "```",
            "",
            "## 模型对比",
            "",
            text_table,
            "",
            "第一层筛选指标：Sharpe、Calmar、盈亏比（Profit Factor）、最大回撤。",
            "",
        ]
        title_map = {
            "factor_analysis": "因子分析",
            "parameter_scan": "参数扫描",
            "walk_forward": "Walk-forward",
        }
        for title, section in (kwargs.get("extra_sections") or {}).items():
            heading = title_map.get(title, title)
            body.extend(["## " + heading, "", "```json", json.dumps(section, indent=2, default=str), "```", ""])
        path.write_text("\n".join(body), encoding="utf-8")
        return path

    report_path = reports / "phase1_report.md"
    _write_md(
        report_path,
        data_meta=data_meta,
        qc=qc,
        summaries=summaries,
        extra_sections=extra,
    )
    _ = original  # keep import used for type checkers

    payload = {
        "data": data_meta,
        "qc": qc,
        "summaries": summaries,
        "report": str(report_path),
        "extra_keys": list(extra.keys()),
    }
    write_json({k: v for k, v in payload.items() if k != "summaries"}, reports / "run_meta.json")
    print(text if False else _table_markdown(table))
    print(f"\nReport: {report_path}")
    return payload


def main(argv: list[str] | None = None) -> int:
    try:
        run(argv)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
