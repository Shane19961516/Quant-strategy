"""Streamlit interactive dashboard for short-strangle vol-arb monitoring."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
from plotly.subplots import make_subplots

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.screener import run_screener
from data_fetcher.market_data import (
    MarketDataClient,
    snapshot_to_ohlc,
    snapshot_vol_series,
)

st.set_page_config(
    page_title="宽跨式波动率套利监控",
    page_icon="📉",
    layout="wide",
    initial_sidebar_state="expanded",
)

API_BASE = st.sidebar.text_input("API Base URL", value="http://127.0.0.1:8000")
USE_LOCAL = st.sidebar.checkbox("本地引擎（无需 API）", value=True)


@st.cache_resource
def get_client() -> MarketDataClient:
    return MarketDataClient(use_demo=True)


def fetch_candidates_local():
    client = get_client()
    snaps = client.fetch_snapshots()
    return run_screener(snaps), {s.underlying: s for s in snaps}


def fetch_candidates_api():
    try:
        r = requests.post(f"{API_BASE}/api/v1/screener/run", timeout=30)
        r.raise_for_status()
        data = r.json()
        return data.get("candidates", []), None
    except Exception as exc:
        st.error(f"API 不可用: {exc} — 请勾选「本地引擎」或启动 FastAPI")
        return [], None


def build_dual_chart(snap, call_strike: float, put_strike: float, lookback: int = 120):
    ohlc = snapshot_to_ohlc(list(snap.prices), lookback=lookback)
    vol = snapshot_vol_series(snap, lookback=lookback)

    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.06,
        row_heights=[0.62, 0.38],
        subplot_titles=("标的价格 · K线 / 通道 / 行权价", "波动率 · IV vs HV30"),
    )

    fig.add_trace(
        go.Candlestick(
            x=ohlc["date"],
            open=ohlc["open"],
            high=ohlc["high"],
            low=ohlc["low"],
            close=ohlc["close"],
            name="K线",
            increasing_line_color="#c0392b",
            decreasing_line_color="#1e8449",
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=ohlc["date"],
            y=ohlc["donchian_high"],
            name="Donchian High",
            line=dict(width=1, dash="dot", color="#7f8c8d"),
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=ohlc["date"],
            y=ohlc["donchian_low"],
            name="Donchian Low",
            line=dict(width=1, dash="dot", color="#7f8c8d"),
            fill="tonexty",
            fillcolor="rgba(127,140,141,0.08)",
        ),
        row=1,
        col=1,
    )
    # Call / Put strike dashed lines
    fig.add_hline(
        y=call_strike,
        line=dict(color="#e74c3c", width=1.5, dash="dash"),
        annotation_text=f"卖出Call K={call_strike:.0f}",
        annotation_position="top right",
        row=1,
        col=1,
    )
    fig.add_hline(
        y=put_strike,
        line=dict(color="#27ae60", width=1.5, dash="dash"),
        annotation_text=f"卖出Put K={put_strike:.0f}",
        annotation_position="bottom right",
        row=1,
        col=1,
    )

    fig.add_trace(
        go.Scatter(
            x=vol["date"],
            y=vol["iv"],
            name="IV",
            line=dict(color="#2980b9", width=2),
        ),
        row=2,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=vol["date"],
            y=vol["hv30"],
            name="HV30",
            line=dict(color="#d35400", width=1.5, dash="dash"),
        ),
        row=2,
        col=1,
    )

    fig.update_layout(
        height=720,
        margin=dict(l=40, r=30, t=50, b=30),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        xaxis_rangeslider_visible=False,
        template="plotly_white",
        paper_bgcolor="#f7f5f2",
        plot_bgcolor="#f7f5f2",
        font=dict(family="IBM Plex Sans, Source Han Sans SC, sans-serif"),
    )
    fig.update_yaxes(title_text="价格", row=1, col=1)
    fig.update_yaxes(title_text="波动率", tickformat=".0%", row=2, col=1)
    return fig


def main():
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;600&family=IBM+Plex+Serif:wght@600&display=swap');
        html, body, [class*="css"] { font-family: 'IBM Plex Sans', sans-serif; }
        h1 { font-family: 'IBM Plex Serif', serif !important; letter-spacing: -0.02em; }
        .metric-card {
            background: linear-gradient(145deg, #f0ebe3 0%, #e4ddd2 100%);
            border-radius: 4px; padding: 0.85rem 1rem; border-left: 3px solid #1a5276;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.title("宽跨式 · 波动率套利决策监控")
    st.caption("Black-76 · Short Strangle Screener · Greeks & Margin Risk")

    snap_map = {}
    if USE_LOCAL:
        candidates, snap_map = fetch_candidates_local()
        cand_dicts = [c.to_dict() for c in candidates]
    else:
        cand_dicts, _ = fetch_candidates_api()
        snap_map = {s.underlying: s for s in get_client().fetch_snapshots()}

    if not cand_dicts:
        st.warning("当前无符合条件的宽跨式候选（DTE 30–45 / IVR>50 / IVP>70 / IV-HV>5%）。")
        st.stop()

    labels = [
        f"{c['underlying']}  |  IVR {c['iv_rank']:.0f}%  POP {c['pop']:.1%}  ROI {c['expected_roi']:.1f}%"
        for c in cand_dicts
    ]
    idx = st.selectbox("品种选择（筛选结果）", range(len(labels)), format_func=lambda i: labels[i])
    c = cand_dicts[idx]

    m1, m2, m3, m4, m5, m6, m7 = st.columns(7)
    m1.metric("标的价 F", f"{c['F']:.2f}")
    m2.metric("IVR", f"{c['iv_rank']:.1f}%")
    m3.metric("IVP", f"{c['iv_percentile']:.1f}%")
    m4.metric("POP", f"{c['pop'] * 100:.1f}%")
    m5.metric("可开手数", f"{c['max_pairs']} 对")
    m6.metric("组合 ROI", f"{c['expected_roi']:.1f}%")
    m7.metric("IV-HV", f"{c['iv_hv_spread'] * 100:.1f}%")

    st.markdown(
        f"**Call** `{c['call_symbol']}`  K={c['call_strike']:.0f}  Δ={c['call_delta']:.3f}  &nbsp;&nbsp;|&nbsp;&nbsp; "
        f"**Put** `{c['put_symbol']}`  K={c['put_strike']:.0f}  Δ={c['put_delta']:.3f}  &nbsp;&nbsp;|&nbsp;&nbsp; "
        f"DTE={c['dte']}  保证金≈{c['total_margin']:,.0f}"
    )

    snap = snap_map.get(c["underlying"])
    if snap is not None:
        fig = build_dual_chart(snap, c["call_strike"], c["put_strike"])
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("无本地行情快照，无法绘制双轴图。请使用本地引擎模式。")

    col_a, col_b, col_c = st.columns(3)
    with col_a:
        if st.button("加入观察名单", use_container_width=True):
            try:
                requests.post(
                    f"{API_BASE}/api/v1/charts/watchlist",
                    params={
                        "underlying": c["underlying"],
                        "call_symbol": c["call_symbol"],
                        "put_symbol": c["put_symbol"],
                        "note": "from streamlit",
                    },
                    timeout=10,
                )
                st.success(f"已加入观察名单: {c['underlying']}")
            except Exception:
                st.info(f"本地记录观察: {c['underlying']} / {c['call_symbol']} + {c['put_symbol']}")
    with col_b:
        if st.button("生成下单报单文本", use_container_width=True):
            ticket = (
                f"【宽跨式卖出报单】\n"
                f"标的: {c['underlying']}  现价: {c['F']:.2f}\n"
                f"卖出 Call: {c['call_symbol']}  K={c['call_strike']:.0f}  Δ={c['call_delta']:.3f}\n"
                f"卖出 Put : {c['put_symbol']}  K={c['put_strike']:.0f}  Δ={c['put_delta']:.3f}\n"
                f"建议手数: {c['max_pairs']} 对  保证金≈{c['total_margin']:.0f}  ROI≈{c['expected_roi']:.1f}%\n"
            )
            st.code(ticket, language="text")
    with col_c:
        st.button("入场决策确认", use_container_width=True, type="primary")

    with st.expander("持仓 Greeks 汇总（API）"):
        try:
            g = requests.get(f"{API_BASE}/api/v1/portfolio/greeks-summary", timeout=10)
            if g.ok:
                st.json(g.json())
            else:
                st.caption("暂无持仓数据 — 先 POST /api/v1/positions/sync")
        except Exception:
            st.caption("API 未启动时跳过 Greeks 汇总")

    with st.expander("全部候选明细"):
        st.dataframe(pd.DataFrame(cand_dicts), use_container_width=True)


if __name__ == "__main__":
    main()
