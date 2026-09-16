"""UPV Phase-1 runner: Yahoo 1h, MVP universe, models A–E, equal-risk portfolio."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from brent_quant.metrics import performance_from_equity
from universal_quant import config as cfg
from universal_quant.adapters.yahoo import get_data
from universal_quant.backtest.engine import run_backtest
from universal_quant.portfolio.risk_budget import equal_risk_nav


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="UPV Engine Phase-1")
    p.add_argument("--interval", default=cfg.INTERVAL)
    p.add_argument("--period", default=cfg.PERIOD)
    p.add_argument("--symbols", default=",".join(cfg.MVP_SYMBOLS))
    p.add_argument("--models", default=",".join(cfg.MODELS))
    p.add_argument("--reports-dir", default=str(cfg.REPORTS_DIR))
    return p.parse_args(argv)


def _md_table(rows: list[dict]) -> str:
    if not rows:
        return "_empty_"
    keys = list(rows[0].keys())
    lines = ["| " + " | ".join(keys) + " |", "| " + " | ".join("---" for _ in keys) + " |"]
    for r in rows:
        cells = []
        for k in keys:
            v = r[k]
            if isinstance(v, float):
                cells.append("" if v != v else f"{v:.4f}")
            else:
                cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def run(argv=None) -> dict:
    args = parse_args(argv)
    reports = Path(args.reports_dir)
    reports.mkdir(parents=True, exist_ok=True)
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    models = [m.strip().upper() for m in args.models.split(",") if m.strip()]
    data_meta = {}
    frames = {}
    rows = []
    navs_e = {}
    navs_d = {}
    for sym in symbols:
        print(f"== {sym} {args.interval} ==")
        df, meta = get_data(sym, interval=args.interval, period=args.period)
        frames[sym] = df
        data_meta[sym] = {k: v for k, v in meta.items() if k != "qc"}
        data_meta[sym]["qc_missing"] = meta.get("qc", {}).get("missing_bars")
        spec = cfg.spec_for(sym)
        for model in models + ["BHS", "BO"]:
            res = run_backtest(df, model=model, symbol=sym, spec=spec)
            st = performance_from_equity(res.daily_equity if len(res.daily_equity) else res.equity, res.trades)
            st["symbol"] = sym
            st["name"] = spec["name"]
            st["model"] = model
            st["cluster"] = spec["cluster"]
            rows.append(
                {
                    "品种": spec["name"],
                    "模型": model,
                    "总收益": st.get("total_return"),
                    "Sharpe": st.get("sharpe"),
                    "Calmar": st.get("calmar"),
                    "盈亏比": st.get("profit_factor"),
                    "最大回撤": st.get("max_drawdown"),
                    "交易次数": st.get("n_trades"),
                    "期望收益": st.get("expectancy"),
                }
            )
            if model == "E" and len(res.daily_equity):
                navs_e[spec["name"]] = res.daily_equity
            if model == "D" and len(res.daily_equity):
                navs_d[spec["name"]] = res.daily_equity
            (reports / f"{sym.replace('=', '').replace('-', '')}_{model}_equity.csv").write_text(
                res.daily_equity.to_csv(header=["equity"])
            )
            if not res.trades.empty:
                res.trades.to_csv(reports / f"{sym.replace('=', '').replace('-', '')}_{model}_trades.csv", index=False)

    table = pd.DataFrame(rows)
    table.to_csv(reports / "single_asset_comparison.csv", index=False)

    extra = {}
    for tag, navmap in (("E", navs_e), ("D", navs_d)):
        if len(navmap) >= 2:
            nav_df = pd.concat(navmap, axis=1, sort=True).ffill().dropna(how="all")
            port = equal_risk_nav(nav_df)
            pst = performance_from_equity(port)
            extra[f"portfolio_{tag}"] = {
                "assets": list(navmap.keys()),
                "sharpe": pst.get("sharpe"),
                "total_return": pst.get("total_return"),
                "max_drawdown": pst.get("max_drawdown"),
                "calmar": pst.get("calmar"),
                "ann_vol": pst.get("ann_vol"),
            }
            port.to_csv(reports / f"portfolio_{tag}_nav.csv", header=["equity"])

    report = reports / "upv_phase1_report.md"
    body = [
        "# UPV Engine 第一阶段回测报告",
        "",
        f"周期：`{args.interval}`；下载窗口：`{args.period}`。",
        "",
        "## 数据",
        "",
        "```json",
        json.dumps(data_meta, indent=2, default=str),
        "```",
        "",
        "## 单品种结果",
        "",
        _md_table(rows),
        "",
        "第一层筛选：多数品种正期望、Sharpe、Calmar、盈亏比、最大回撤。",
        "",
        "## 等风险组合",
        "",
        "```json",
        json.dumps(extra, indent=2, default=str),
        "```",
        "",
    ]
    report.write_text("\n".join(body), encoding="utf-8")
    Path(reports / "run_meta.json").write_text(json.dumps({"data": data_meta, "portfolio": extra}, indent=2, default=str), encoding="utf-8")
    print(table.to_string(index=False))
    print("Report:", report)
    return {"rows": rows, "data": data_meta, "portfolio": extra, "report": str(report)}


def main(argv=None) -> int:
    try:
        run(argv)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
