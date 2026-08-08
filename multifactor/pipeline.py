# -*- coding: utf-8 -*-
"""End-to-end multi-factor pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .backtest import BacktestResult, run_backtest
from .combine import combine_factors, rolling_icir_weights
from .data import MarketPanel, REPO_ROOT, load_market_panel
from .factors import DEFAULT_FACTORS, DEFAULT_FACTOR_NAMES, build_factor_panel
from .metrics import factor_ic_summary, format_summary, quantile_returns
from .portfolio import scores_to_weights


@dataclass
class PipelineResult:
    panel: MarketPanel
    factors: Dict[str, pd.DataFrame]
    composite: pd.DataFrame
    weights: pd.DataFrame
    backtest: BacktestResult
    ic_table: pd.DataFrame
    quantile_rets: pd.DataFrame
    blend_weights: Optional[pd.DataFrame] = None
    single_factor_summaries: pd.DataFrame = field(default_factory=pd.DataFrame)

    def to_frames(self) -> Dict[str, pd.DataFrame]:
        frames = {
            "composite": self.composite,
            "ic_table": self.ic_table.reset_index(),
            "quantile_rets": self.quantile_rets,
            "single_factor_summaries": self.single_factor_summaries,
        }
        frames.update(self.backtest.to_frames())
        if self.blend_weights is not None:
            frames["blend_weights"] = self.blend_weights
        for name, f in self.factors.items():
            frames[f"factor_{name}"] = f
        return frames


def run_multifactor_pipeline(
    root: Path | str | None = None,
    start: str = "2010-01-01",
    end: str | None = None,
    universe: str = "intersect",
    factor_names: Optional[List[str]] = None,
    combine_method: str = "equal",  # equal | icir
    portfolio_method: str = "long_short",
    n_quantiles: int = 5,
    long_only_top_pct: float = 0.2,
    top_n: Optional[int] = None,
    cost_bps: float = 20.0,
    neutralize_industry: bool = True,
    max_industry_weight: Optional[float] = 0.25,
    icir_window: int = 24,
    panel: Optional[MarketPanel] = None,
) -> PipelineResult:
    root = Path(root) if root is not None else REPO_ROOT
    if panel is None:
        panel = load_market_panel(root=root, start=start, end=end, universe=universe)

    names = factor_names or list(DEFAULT_FACTOR_NAMES)
    factors = build_factor_panel(
        panel.returns,
        panel.industry,
        factor_names=names,
        neutralize_industry=neutralize_industry,
    )

    blend_weights = None
    if combine_method == "icir":
        blend = rolling_icir_weights(factors, panel.returns, window=icir_window)
        composite = blend["combined"]
        blend_weights = blend["weights"]
    else:
        composite = combine_factors(factors, names=names)

    # optional: restrict tradable universe to names with finite score
    weights = scores_to_weights(
        composite,
        method=portfolio_method,  # type: ignore[arg-type]
        n_quantiles=n_quantiles,
        top_n=top_n,
        long_only_top_pct=long_only_top_pct,
        industry=panel.industry if max_industry_weight else None,
        max_industry_weight=max_industry_weight if portfolio_method == "long_only" else None,
    )

    bt = run_backtest(
        weights,
        panel.returns,
        cost_bps=cost_bps,
        benchmark=panel.benchmark if portfolio_method == "long_only" else None,
    )

    ic_table = factor_ic_summary(factors, panel.returns)
    # also add composite IC
    from .metrics import rank_ic_series

    comp_ic = rank_ic_series(composite, panel.returns)
    ic_table.loc["composite"] = {
        "ic_mean": float(comp_ic.mean()),
        "ic_std": float(comp_ic.std(ddof=0)),
        "icir": float(comp_ic.mean() / comp_ic.std(ddof=0))
        if comp_ic.std(ddof=0) > 0
        else np.nan,
        "ic_pos_ratio": float((comp_ic > 0).mean()),
        "n": float(comp_ic.notna().sum()),
    }

    qrets = quantile_returns(composite, panel.returns, n_quantiles=n_quantiles)

    # single-factor long-short diagnostics
    rows = []
    for name, fpanel in factors.items():
        w = scores_to_weights(fpanel, method="long_short", n_quantiles=n_quantiles)
        sbt = run_backtest(w, panel.returns, cost_bps=cost_bps)
        rows.append({"factor": name, **sbt.summary})
    single = pd.DataFrame(rows).set_index("factor")

    return PipelineResult(
        panel=panel,
        factors=factors,
        composite=composite,
        weights=weights,
        backtest=bt,
        ic_table=ic_table,
        quantile_rets=qrets,
        blend_weights=blend_weights,
        single_factor_summaries=single,
    )


def save_results(
    result: PipelineResult,
    out_dir: Path | str,
    prefix: str = "mf",
) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    frames = result.to_frames()
    # avoid dumping full factor matrices by default (large); save key outputs
    keep = [
        "equity",
        "returns",
        "gross_returns",
        "cost",
        "turnover",
        "summary",
        "ic_table",
        "quantile_rets",
        "single_factor_summaries",
        "blend_weights",
        "excess_returns",
        "benchmark",
    ]
    for k in keep:
        if k in frames and frames[k] is not None and not frames[k].empty:
            frames[k].to_csv(out / f"{prefix}_{k}.csv", encoding="utf-8-sig")

    # latest holdings snapshot
    w = result.weights
    last = w.columns[-1]
    snap = w[last]
    snap = snap[snap.abs() > 0].sort_values(ascending=False)
    snap.to_csv(out / f"{prefix}_holdings_{pd.Timestamp(last).date()}.csv", encoding="utf-8-sig")

    _plot_nav(result, out / f"{prefix}_nav.png")
    _plot_ic(result, out / f"{prefix}_ic.png")
    _plot_quantiles(result, out / f"{prefix}_quantiles.png")

    # text summary
    text = format_summary(result.backtest.summary)
    text += "\n\n因子 IC 摘要\n"
    text += result.ic_table.to_string(float_format=lambda x: f"{x:.4f}")
    text += "\n\n单因子多空绩效\n"
    cols = [c for c in ["cagr", "sharpe", "max_drawdown", "win_rate"] if c in result.single_factor_summaries]
    text += result.single_factor_summaries[cols].to_string(float_format=lambda x: f"{x:.4f}")
    (out / f"{prefix}_summary.txt").write_text(text, encoding="utf-8")
    return out


def _plot_nav(result: PipelineResult, path: Path) -> None:
    eq = result.backtest.equity.dropna()
    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.plot(eq.index, eq.values, color="#0B3D5C", lw=1.8, label="Strategy NAV")
    if result.backtest.benchmark is not None:
        bm = result.backtest.benchmark.reindex(eq.index).dropna()
        if len(bm):
            bm_nav = (1.0 + bm).cumprod()
            # align start
            bm_nav = bm_nav / bm_nav.iloc[0] * eq.iloc[0]
            ax.plot(bm_nav.index, bm_nav.values, color="#C45C26", lw=1.2, alpha=0.85, label="CSI300")
    ax.set_title("Multi-Factor Strategy NAV")
    ax.set_ylabel("NAV")
    ax.grid(True, alpha=0.25)
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def _plot_ic(result: PipelineResult, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(8, 4))
    tab = result.ic_table.drop(index=["composite"], errors="ignore")
    ax.bar(tab.index.astype(str), tab["ic_mean"].values, color="#0B3D5C", alpha=0.85)
    ax.axhline(0, color="gray", lw=0.8)
    ax.set_title("Mean Rank IC by Factor")
    ax.set_ylabel("IC")
    ax.tick_params(axis="x", rotation=30)
    ax.grid(True, axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def _plot_quantiles(result: PipelineResult, path: Path) -> None:
    q = result.quantile_rets.dropna(how="all")
    if q.empty:
        return
    cum = (1.0 + q.fillna(0.0)).cumprod()
    fig, ax = plt.subplots(figsize=(10, 4.5))
    colors = plt.cm.coolwarm(np.linspace(0.15, 0.85, cum.shape[1]))
    for i, col in enumerate(cum.columns):
        ax.plot(cum.index, cum[col].values, label=col, color=colors[i], lw=1.4)
    ax.set_title("Composite Score Quantile Cumulative Return")
    ax.legend(frameon=False, ncol=min(5, cum.shape[1]))
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
