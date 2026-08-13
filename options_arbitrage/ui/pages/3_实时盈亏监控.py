"""风控台 — 对齐用户 Excel：概览 / 希腊值压力测试与归因 / 分品种明细。"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ui.common import account_id, get_json, inject_sidebar, session_date

st.set_page_config(page_title="风控台", layout="wide")
inject_sidebar()

st.markdown(
    """
    <style>
    .block-container { padding-top: 1rem; padding-bottom: 1rem; max-width: 1400px; }
    div[data-testid="stMetricValue"] { font-size: 1.35rem; }
    .cockpit-title { font-size: 1.45rem; font-weight: 700; margin: 0 0 0.4rem 0; }
    .panel {
      border: 1px solid #c5ced6; background: #f7f9fb; padding: 0.75rem 0.9rem;
      border-radius: 2px; margin-bottom: 0.6rem; min-height: 210px;
    }
    .panel h4 { margin: 0 0 0.55rem 0; font-size: 0.95rem; color: #1f3a4d;
      border-bottom: 2px solid #1f4e79; padding-bottom: 0.25rem; }
    .kv { display: flex; justify-content: space-between; font-size: 0.92rem;
      padding: 0.12rem 0; border-bottom: 1px dotted #d7dee5; }
    .kv .lab { color: #445; }
    .kv .val { font-weight: 650; font-variant-numeric: tabular-nums; }
    .pos { color: #111; } .neg { color: #c0392b; }
    .hdr { background:#1f4e79; color:#fff; padding:0.35rem 0.6rem; font-weight:600; }
    </style>
    """,
    unsafe_allow_html=True,
)

sess = session_date()
acct = account_id()
if not sess:
    st.warning("请设置监控交易日")
    st.stop()

top_l, top_r = st.columns([3, 1])
with top_l:
    st.markdown('<div class="cockpit-title">风控台 · 当日盈亏 / 希腊值 / 分品种</div>', unsafe_allow_html=True)
with top_r:
    if st.button("刷新", type="primary", use_container_width=True):
        st.rerun()

try:
    data = get_json(
        "/api/v1/settlement/risk-cockpit",
        {"account_id": acct, "session_date": sess, "daily_profit_target": 660},
    )
except Exception as exc:  # noqa: BLE001
    st.error(f"读取失败（请确认 API 已启动）: {exc}")
    st.stop()

ov = data.get("风控概览") or {}
gk = data.get("希腊值风控") or {}
st_ = data.get("压力测试") or {}
attr = data.get("盈亏归因") or {}


def _fmt(v, money=True, signed=True):
    try:
        x = float(v)
    except Exception:
        return str(v)
    cls = "neg" if x < 0 else "pos"
    if money:
        s = f"{x:+,.2f}" if signed else f"{x:,.2f}"
    else:
        s = f"{x:+.4f}" if signed else f"{x:.4f}"
    return f'<span class="{cls}">{s}</span>'


def _kv(lab, val_html):
    return f'<div class="kv"><span class="lab">{lab}</span><span class="val">{val_html}</span></div>'


# -------- 三栏概览（对齐 Excel 顶部）--------
c1, c2, c3 = st.columns([1.15, 1.35, 1.0])

with c1:
    html = [
        '<div class="panel"><h4>风控概览</h4>',
        _kv("昨日持仓损益", _fmt(ov.get("昨日持仓损益"))),
        _kv("今日成交损益", _fmt(ov.get("今日成交损益"))),
        _kv("手续费", _fmt(ov.get("手续费"))),
        _kv("= 套利策略损益", _fmt(ov.get("套利策略损益"))),
        _kv("对冲盈亏", _fmt(ov.get("对冲盈亏"))),
        _kv("综合(含对冲)", _fmt(ov.get("综合盈亏_含对冲"))),
        _kv("保证金合计(万)", _fmt(ov.get("保证金合计_万"), signed=False)),
        _kv("日均盈利目标", _fmt(ov.get("日均盈利目标"), signed=False)),
        _kv("距目标", _fmt(ov.get("距目标"))),
        _kv("Δ名义价值总额", _fmt(ov.get("delta名义价值总额"), signed=False)),
        "</div>",
    ]
    st.markdown("\n".join(html), unsafe_allow_html=True)

with c2:
    html = [
        '<div class="panel"><h4>希腊值风控　|　压力测试5%　|　盈亏归因</h4>',
        '<div class="kv"><span class="lab"></span>'
        '<span class="val">希腊值&nbsp;&nbsp;|&nbsp;&nbsp;压力测试&nbsp;&nbsp;|&nbsp;&nbsp;归因</span></div>',
        _kv(
            "Delta",
            f'{_fmt(gk.get("组合净Δ"), money=False)} &nbsp;|&nbsp; {_fmt(st_.get("delta"))} &nbsp;|&nbsp; {_fmt(attr.get("delta"))}',
        ),
        _kv(
            "Gamma",
            f'{_fmt(gk.get("组合净Γ"), money=False)} &nbsp;|&nbsp; {_fmt(st_.get("gamma"))} &nbsp;|&nbsp; {_fmt(attr.get("gamma"))}',
        ),
        _kv(
            "日Theta",
            f'{_fmt(gk.get("日Theta"))} &nbsp;|&nbsp; {_fmt(st_.get("theta"))} &nbsp;|&nbsp; {_fmt(attr.get("theta"))}',
        ),
        _kv(
            "Vega",
            f'{_fmt(gk.get("Vega"))} &nbsp;|&nbsp; {_fmt(st_.get("vega"))} &nbsp;|&nbsp; {_fmt(attr.get("vega"))}',
        ),
        _kv("压力亏损总额", _fmt(st_.get("total"))),
        _kv("归因合计", _fmt(attr.get("total"))),
        f'<div style="margin-top:0.4rem;font-size:0.8rem;color:#667;">'
        f'压力: 标的±{st_.get("shock_pct", 5)}%取劣侧 + IV冲击{st_.get("iv_shock_pts", 5)}点</div>',
        "</div>",
    ]
    st.markdown("\n".join(html), unsafe_allow_html=True)

with c3:
    html = [
        '<div class="panel"><h4>保证金占用</h4>',
        _kv("结算保证金", _fmt(ov.get("保证金合计"), signed=False)),
        _kv("可用资金", _fmt(data.get("available"), signed=False)),
        _kv("风险度", f'{float(data.get("risk_degree") or 0):.2f}%'),
        _kv("品种保证金最大占比", f'{ov.get("品种保证金最大占比", 0)}%（{ov.get("最大占比品种","-")}）'),
        _kv("配置上限", f'{ov.get("配置上限占比", 12)}%'),
        _kv("期初权益", _fmt(data.get("opening_equity"), signed=False)),
        "</div>",
    ]
    st.markdown("\n".join(html), unsafe_allow_html=True)

if data.get("alerts"):
    for a in data["alerts"][:6]:
        st.warning(a)

# -------- 分品种明细表（对齐 Excel 下部）--------
st.markdown('<div class="hdr">分品种明细（BS76 delta / 昨仓+今成交损益 / 预估损益 / 保证金）</div>', unsafe_allow_html=True)
rows = data.get("分品种明细") or []
if rows:
    df = pd.DataFrame(rows)
    show_cols = [
        "合约",
        "汇总delta",
        "套利策略delta(张数)",
        "持仓delta张数汇总",
        "品种盈亏",
        "昨仓损益",
        "今成交损益",
        "套利策略",
        "预估损益",
        "保证金",
        "乘数",
        "F",
        "净卖持仓",
        "昨仓短",
        "今开短",
        "risk_status",
    ]
    view = df[[c for c in show_cols if c in df.columns]].copy()
    st.dataframe(view, use_container_width=True, hide_index=True, height=260)
else:
    st.info("无分品种数据")

meth = data.get("methodology") or {}
if meth:
    st.caption(
        f"模型：{data.get('model') or meth.get('greeks_model', 'BS76')}｜"
        f"{meth.get('pnl', '')}｜"
        f"{meth.get('price_basis', '')}｜"
        f"{meth.get('delta_lots', '')}"
    )

# -------- 对冲明细 + 品种净持仓 --------
h1, h2 = st.columns(2)
with h1:
    st.markdown("**对冲成交（期货）**")
    hedge = data.get("对冲明细") or []
    if hedge:
        st.dataframe(pd.DataFrame(hedge), use_container_width=True, hide_index=True)
        st.caption(f"对冲盈亏合计：{ov.get('对冲盈亏'):,.2f}")
    else:
        st.caption("暂无期货对冲成交 — 可在 API `/futures-trades` 录入")
with h2:
    st.markdown("**分品种净持仓（昨仓+今成交）**")
    net = data.get("分品种净持仓") or []
    if net:
        ndf = pd.DataFrame(net)
        cols = ["product", "short_volume", "long_volume", "net_volume", "net_delta", "net_vega", "net_theta", "margin", "risk_status"]
        st.dataframe(ndf[[c for c in cols if c in ndf.columns]], use_container_width=True, hide_index=True)

with st.expander("行情同步（akshare / CTP）— 昨收 + 最新价", expanded=True):
    st.caption(
        "数据源规则：①上一交易日收盘价(prev_close)；"
        "②当前最新价(last)——交易时段内实时，非交易时段默认用最近已收盘交易日收盘价。"
        "写入 marks / __CLOSE__ / __F__ 后刷新风控台。"
    )
    prov = st.selectbox("行情源", ["akshare", "ctp"], index=0)
    if st.button("从行情源同步并写入 marks", type="primary"):
        try:
            from ui.common import post_json

            with st.spinner("正在拉取行情…"):
                r = post_json(
                    "/api/v1/settlement/sync-quotes",
                    {
                        "account_id": acct,
                        "session_date": sess,
                        "provider": prov,
                        "persist": True,
                    },
                )
            st.success(
                f"provider={r.get('provider')} · in_session={r.get('in_session')} · "
                f"写入 {r.get('persisted')} 条 · 错误 {len(r.get('errors') or [])}"
            )
            qdf = pd.DataFrame(r.get("quotes") or [])
            if not qdf.empty:
                show = [
                    c
                    for c in [
                        "symbol",
                        "kind",
                        "prev_close",
                        "last",
                        "last_bar_date",
                        "in_session",
                        "source",
                        "note",
                    ]
                    if c in qdf.columns
                ]
                st.dataframe(qdf[show], use_container_width=True, hide_index=True)
            if r.get("errors"):
                st.warning(pd.DataFrame(r["errors"]))
            st.rerun()
        except Exception as exc:  # noqa: BLE001
            st.error(f"同步失败: {exc}")

with st.expander("导入最新价 marks（对齐 Libra rt_price）", expanded=False):
    st.caption(
        "差额主因：本系统曾把结算价当成最新价。请粘贴 Libra「数据总览」的 rt_price，"
        "或 JSON如 {\"AP610C8200\": 30, \"__F__:AP610\": 7855}。"
    )
    raw_marks = st.text_area(
        "marks JSON",
        height=140,
        placeholder='{"AP610C8200":30,"AP610P7500":25,"__F__:JD2610":3907}',
    )
    c_imp1, c_imp2 = st.columns(2)
    with c_imp1:
        if st.button("写入 marks", type="primary"):
            import json as _json

            try:
                payload = _json.loads(raw_marks.strip() or "{}")
                if not isinstance(payload, dict) or not payload:
                    st.error("请提供非空 JSON 对象")
                else:
                    from ui.common import post_json

                    r = post_json(
                        "/api/v1/settlement/marks/batch",
                        {
                            "account_id": acct,
                            "session_date": sess,
                            "marks": {str(k): float(v) for k, v in payload.items()},
                        },
                    )
                    st.success(f"已更新 {r.get('updated')} 条最新价")
                    st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(f"导入失败: {exc}")
    with c_imp2:
        try:
            from ui.common import get_json as _gj

            cur = _gj("/api/v1/settlement/marks", {"account_id": acct, "session_date": sess})
            st.caption(f"当前已存最新价 {cur.get('count', 0)} 条")
            if cur.get("marks"):
                st.dataframe(
                    pd.DataFrame(
                        [{"symbol": k, "last": v} for k, v in sorted(cur["marks"].items())]
                    ),
                    use_container_width=True,
                    hide_index=True,
                    height=160,
                )
        except Exception:
            st.caption("暂无 marks 或 API 未就绪")

with st.expander("今日成交逐笔损益（方向计，不冲抵）"):
    by_trade = data.get("今日成交逐笔") or []
    if by_trade:
        st.dataframe(pd.DataFrame(by_trade), use_container_width=True, hide_index=True)
    else:
        st.caption("暂无今日期权成交")

with st.expander("期权合约盈亏明细 / 标记价"):
    pnl = data.get("pnl_report") or {}
    legs = pnl.get("by_leg") or []
    if legs:
        st.dataframe(pd.DataFrame(legs), use_container_width=True, hide_index=True)

with st.expander("希腊值分腿明细 (BS76)"):
    gs = data.get("greeks_summary") or {}
    by_leg = gs.get("leg_greeks") or []
    if by_leg:
        st.dataframe(pd.DataFrame(by_leg), use_container_width=True, hide_index=True)

st.caption(
    f"结算日 {data.get('settlement_date')} · 监控日 {data.get('session_date')} · "
    f"目标日均盈利 {ov.get('日均盈利目标')} · 压力测试标的±{st_.get('shock_pct')}%"
)
