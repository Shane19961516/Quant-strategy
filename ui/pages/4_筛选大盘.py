"""Page: 宽跨式筛选大盘（原功能）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.screener import run_screener
from data_fetcher.market_data import MarketDataClient, snapshot_to_ohlc, snapshot_vol_series
from ui.common import account_id, get_json, inject_sidebar

st.set_page_config(page_title="筛选大盘", layout="wide")
inject_sidebar()

st.title("④ 宽跨式筛选大盘")
st.caption("DTE 30–45 · IVR>50% · IVP>70% · IV-HV>5% · Δ≈±0.20")


@st.cache_resource
def _client() -> MarketDataClient:
    return MarketDataClient(use_demo=True)


# margin usage from live settlement if available
margin_used = 0.0
try:
    active = get_json("/api/v1/settlement/active", {"account_id": account_id()})
    margin_used = float(active.get("margin_occupied") or 0)
    st.caption(f"当前结算保证金占用 {margin_used:,.0f}（用于开仓上限风控）")
except Exception:
    pass

snaps = _client().fetch_snapshots()
candidates = run_screener(snaps, current_margin_used=margin_used)
snap_map = {s.underlying: s for s in snaps}

if not candidates:
    st.warning("无符合条件候选")
    st.stop()

cand_dicts = [c.to_dict() for c in candidates]
labels = [
    f"{c['underlying']} | IVR {c['iv_rank']:.0f}% | POP {c['pop']:.1%} | ROI {c['expected_roi']:.1f}% | 手数 {c['max_pairs']}"
    for c in cand_dicts
]
idx = st.selectbox("候选品种", range(len(labels)), format_func=lambda i: labels[i])
c = cand_dicts[idx]

m = st.columns(7)
m[0].metric("F", f"{c['F']:.2f}")
m[1].metric("IVR", f"{c['iv_rank']:.1f}%")
m[2].metric("IVP", f"{c['iv_percentile']:.1f}%")
m[3].metric("POP", f"{c['pop']*100:.1f}%")
m[4].metric("可开", f"{c['max_pairs']} 对")
m[5].metric("ROI", f"{c['expected_roi']:.1f}%")
m[6].metric("IV-HV", f"{c['iv_hv_spread']*100:.1f}%")

if c.get("blocked_by_margin_cap"):
    st.error("账户保证金占用超限 — 已拒绝新开仓手数建议")

st.markdown(
    f"Call `{c['call_symbol']}` K={c['call_strike']:.0f} Δ={c['call_delta']:.3f} &nbsp;|&nbsp; "
    f"Put `{c['put_symbol']}` K={c['put_strike']:.0f} Δ={c['put_delta']:.3f}"
)

snap = snap_map[c["underlying"]]
ohlc = snapshot_to_ohlc(list(snap.prices), lookback=120)
vol = snapshot_vol_series(snap, lookback=120)
fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.62, 0.38], vertical_spacing=0.06)
fig.add_trace(
    go.Candlestick(
        x=ohlc["date"], open=ohlc["open"], high=ohlc["high"], low=ohlc["low"], close=ohlc["close"], name="K"
    ),
    row=1,
    col=1,
)
fig.add_hline(y=c["call_strike"], line=dict(color="#c0392b", dash="dash"), row=1, col=1)
fig.add_hline(y=c["put_strike"], line=dict(color="#1e8449", dash="dash"), row=1, col=1)
fig.add_trace(go.Scatter(x=vol["date"], y=vol["iv"], name="IV", line=dict(color="#2471a3", width=2)), row=2, col=1)
fig.add_trace(
    go.Scatter(x=vol["date"], y=vol["hv30"], name="HV30", line=dict(color="#d35400", dash="dash")),
    row=2,
    col=1,
)
fig.update_layout(height=680, xaxis_rangeslider_visible=False, template="plotly_white")
st.plotly_chart(fig, use_container_width=True)

ticket = (
    f"【宽跨式卖出报单】\n标的 {c['underlying']} F={c['F']:.2f}\n"
    f"卖Call {c['call_symbol']} K={c['call_strike']:.0f}\n"
    f"卖Put  {c['put_symbol']} K={c['put_strike']:.0f}\n"
    f"建议 {c['max_pairs']} 对  保证金≈{c['total_margin']:.0f}  ROI≈{c['expected_roi']:.1f}%\n"
)
st.code(ticket)

with st.expander("全部候选"):
    st.dataframe(pd.DataFrame(cand_dicts), use_container_width=True)
