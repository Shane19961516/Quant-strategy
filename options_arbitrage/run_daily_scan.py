#!/usr/bin/env python3
"""Daily short-strangle scan CLI.

Usage:
  python run_daily_scan.py                  # try AkShare, fallback demo
  python run_daily_scan.py --demo-only
  python run_daily_scan.py --live
  python run_daily_scan.py --no-events --relax-technicals
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.screener import run_screener_with_rejects
from data_fetcher.market_data import MarketDataClient, generate_demo_snapshots
from report.daily_report import build_markdown_report


def _load_settings() -> dict:
    import yaml

    with open(ROOT / "config" / "settings.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Short strangle daily screener")
    p.add_argument("--live", action="store_true", help="Force AkShare live fetch")
    p.add_argument("--demo-only", action="store_true", help="Use synthetic demo data only")
    p.add_argument("--no-events", action="store_true", help="Disable event calendar filter")
    p.add_argument("--relax-technicals", action="store_true", help="Skip ranging-regime filter")
    p.add_argument("--relax-liquidity", action="store_true", help="Skip liquidity filter")
    p.add_argument("--output-dir", type=str, default=str(ROOT / "output"))
    return p.parse_args()


def main() -> int:
    args = parse_args()
    settings = _load_settings()
    sc = settings.setdefault("screener", {})
    if args.no_events:
        sc["exclude_events"] = False

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    data_source = "demo"
    snaps = []
    fetch_notes: list[str] = []

    if args.demo_only and not args.live:
        snaps = generate_demo_snapshots()
        data_source = "demo"
    else:
        client = MarketDataClient(use_demo=not args.live, use_akshare=True)
        snaps = client.fetch_snapshots(refresh=True)
        status = client.last_status or {}
        data_source = status.get("source", "unknown")
        if status.get("fallback"):
            data_source = f"{data_source}+{status['fallback']}"
            fetch_notes.append("AkShare 部分/全部失败，已回退演示数据。" + status.get("error", ""))
        if status.get("notes"):
            fetch_notes.extend(status["notes"])
        if status.get("failed"):
            fetch_notes.append("失败品种: " + ", ".join(status["failed"]))

    results, rejects, iv_passed = run_screener_with_rejects(
        snaps,
        settings=settings,
        require_ranging=not args.relax_technicals,
        require_liquidity=not args.relax_liquidity,
        exclude_events=sc.get("exclude_events", True),
    )

    # Watchlist: pass IV/liquidity/POP/margin but fail only technical or events
    watchlist: list[dict] = []
    if not args.relax_technicals:
        relaxed, _, _ = run_screener_with_rejects(
            snaps,
            settings=settings,
            require_ranging=False,
            require_liquidity=not args.relax_liquidity,
            exclude_events=False,
        )
        main_und = {c.underlying for c in results}
        for c in relaxed:
            if c.underlying in main_und:
                continue
            d = c.to_dict()
            d["rationale"] = (
                f"【观察池】IV 条件通过（IVR {c.iv_rank:.0f} / IVP {c.iv_percentile:.0f}，"
                f"IV-HV {c.iv_hv_spread*100:.1f}pt），但技术面或事件过滤未过；"
                f"若转入震荡可关注。胜率约 {c.pop*100:.1f}%，权/保 {c.premium_margin_ratio*100:.1f}%。"
            )
            d["notes"] = (d.get("notes") or "") + "；WATCHLIST_TECH_OR_EVENT"
            watchlist.append(d)

    # Stats by reject stage
    stages = {}
    for r in rejects:
        stages[r.stage] = stages.get(r.stage, 0) + 1

    universe_stats = {
        "scanned": len(snaps),
        "iv_passed": len(iv_passed),
        "liquidity_passed": len(snaps) - stages.get("liquidity", 0),
        "ranging_passed": len(snaps) - stages.get("technical", 0),
        "event_passed": len(snaps) - stages.get("event", 0),
        "recommended": len(results),
        "watchlist": len(watchlist),
        "reject_stages": stages,
    }

    recommendations = []
    for c in results:
        d = c.to_dict()
        d["rationale"] = (
            f"IV Rank {c.iv_rank:.0f} / IVP {c.iv_percentile:.0f}，IV-HV "
            f"{c.iv_hv_spread*100:.1f}pt；震荡格局（ADX={c.adx:.1f}）；"
            f"权利金/保证金 {c.premium_margin_ratio*100:.1f}%；到期胜率约 {c.pop*100:.1f}%。"
        )
        recommendations.append(d)

    # Append watchlist after formal recommendations in report detail section
    report_recs = recommendations + watchlist

    params = settings.get("screener", {})
    params_summary = (
        f"IVR≥{params.get('ivr_min')}或IVP≥{params.get('ivp_min')}, "
        f"DTE {params.get('dte_min')}-{params.get('dte_max')}, "
        f"Δ∈{params.get('delta_target')}, 权/保≥{params.get('min_premium_margin_ratio')}, "
        f"POP≥{params.get('min_pop')}"
    )

    report = build_markdown_report(
        scan_meta={
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "data_source": data_source,
            "params_summary": params_summary,
            "notes": fetch_notes,
        },
        universe_stats=universe_stats,
        iv_passed=iv_passed,
        recommendations=report_recs,
        rejected=[r.to_dict() for r in rejects],
    )
    if fetch_notes:
        report += "\n## 数据说明\n\n" + "\n".join(f"- {n}" for n in fetch_notes) + "\n"

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path = out_dir / f"short_strangle_report_{stamp}.md"
    latest_md = out_dir / "latest_report.md"
    json_path = out_dir / "latest_scan.json"
    md_path.write_text(report, encoding="utf-8")
    latest_md.write_text(report, encoding="utf-8")
    json_path.write_text(
        json.dumps(
            {
                "meta": {"data_source": data_source, "generated_at": stamp, "notes": fetch_notes},
                "stats": universe_stats,
                "iv_passed": iv_passed,
                "recommendations": recommendations,
                "watchlist": watchlist,
                "rejects": [r.to_dict() for r in rejects],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(report)
    print(f"\n[saved] {md_path}")
    print(f"[saved] {latest_md}")
    print(f"[saved] {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
