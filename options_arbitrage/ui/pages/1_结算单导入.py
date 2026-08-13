"""Page: 昨日结算单导入。"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.settlement_parser import parse_settlement_xls
from ui.common import account_id, api_base, inject_sidebar, post_file

st.set_page_config(page_title="结算单导入", layout="wide")
inject_sidebar()

st.title("① 昨日结算单导入")
st.caption("上传经纪商「客户交易结算日报」.xls — 解析资金状况 + 期权持仓汇总。当日成交请到下一页手录。")

uploaded = st.file_uploader("选择结算单 (.xls)", type=["xls", "xlsx"])

col_a, col_b = st.columns([1, 1])
with col_a:
    preview_only = st.checkbox("仅本地预览（不入库）", value=False)
with col_b:
    do_upload = st.button("确认导入到系统", type="primary", disabled=uploaded is None)

if uploaded is not None:
    raw = uploaded.getvalue()
    tmp = ROOT / "data" / "uploads" / f"_preview_{uploaded.name}"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(raw)

    try:
        parsed = parse_settlement_xls(tmp)
    except Exception as exc:  # noqa: BLE001
        st.error(f"解析失败: {exc}")
        st.stop()

    f = parsed.fund
    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("账号", f.account_id)
    m2.metric("结算日", f.trade_date)
    m3.metric("客户权益", f"{f.client_equity:,.2f}")
    m4.metric("保证金占用", f"{f.margin_occupied:,.2f}")
    m5.metric("可用资金", f"{f.available:,.2f}")
    m6.metric("风险度", f"{f.risk_degree:.2f}%")

    st.subheader("昨日期权持仓（期权持仓汇总）")
    pos_df = pd.DataFrame([p.to_dict() for p in parsed.option_positions])
    if not pos_df.empty:
        show_cols = [
            "symbol",
            "underlying",
            "option_type",
            "strike",
            "long_volume",
            "short_volume",
            "short_avg_price",
            "prev_settle",
            "settle_price",
            "margin",
            "multiplier",
        ]
        st.dataframe(pos_df[show_cols], use_container_width=True, hide_index=True)
        st.caption(
            f"共 {len(pos_df)} 条 · 卖持仓 {int(pos_df['short_volume'].sum())} 手 · "
            f"买持仓 {int(pos_df['long_volume'].sum())} 手 · 保证金合计 {pos_df['margin'].sum():,.2f}"
        )
    else:
        st.warning("未解析到期权持仓")

    with st.expander("结算单内期权成交明细（仅供核对，不会写入「当日成交」表）"):
        if parsed.option_trades:
            st.dataframe(pd.DataFrame([t.to_dict() for t in parsed.option_trades]), use_container_width=True)
            st.info("昨日结算单里的成交属于结算日当天历史；监控「今日」请在【当日成交录入】页手录。")
        else:
            st.write("无")

    if do_upload:
        if preview_only:
            st.warning("已勾选「仅本地预览」— 取消勾选后再点确认导入。")
        else:
            try:
                resp = post_file(
                    "/api/v1/settlement/upload",
                    raw,
                    uploaded.name,
                    form={"account_id": account_id() or f.account_id},
                )
                st.success(
                    f"导入成功 · import_id={resp['import_id']} · 持仓 {resp['position_count']} 条 · "
                    f"建议监控日 {resp['suggested_session_date']}"
                )
                st.json(resp)
                st.session_state["session_date"] = resp["suggested_session_date"]
                st.session_state["account_id"] = resp["account_id"]
            except Exception as exc:  # noqa: BLE001
                st.error(f"上传失败（请确认 API 已启动 {api_base()}）: {exc}")
