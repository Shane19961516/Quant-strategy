"""Page: 昨日结算单导入。"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.settlement_parser import parse_settlement_xls
from ui.common import account_id, api_base, inject_sidebar, post_file

st.set_page_config(page_title="结算单导入", layout="wide")
inject_sidebar()

st.title("① 昨日结算单导入")
st.caption("上传经纪商「客户交易结算日报」.xls，或从中国期货市场监控中心自动拉取「逐日盯市」结算单作为昨仓。")

st.subheader("从监控中心自动同步（逐日盯市）")
c1, c2, c3 = st.columns([1, 1, 1])
with c1:
    cfmmc_user = st.text_input(
        "CFMMC 查询账号",
        value=st.session_state.get("cfmmc_user", ""),
        placeholder="默认读环境变量 CFMMC_USER",
    )
with c2:
    cfmmc_pwd = st.text_input(
        "CFMMC 密码",
        value="",
        placeholder="默认读环境变量 CFMMC_PASSWORD",
        type="password",
    )
with c3:
    cfmmc_date = st.text_input(
        "结算日期",
        value=st.session_state.get("cfmmc_trade_date", ""),
        placeholder="留空=上一交易日 YYYY-MM-DD",
    )
skip_same = st.checkbox("若已有同日有效结算单则跳过", value=True)
if st.button("登录监控中心并导入昨仓", type="primary"):
    payload = {
        "account_id": account_id() or None,
        "trade_date": cfmmc_date or None,
        "skip_if_same_date": skip_same,
    }
    if cfmmc_user.strip():
        payload["user"] = cfmmc_user.strip()
        st.session_state["cfmmc_user"] = cfmmc_user.strip()
    if cfmmc_pwd:
        payload["password"] = cfmmc_pwd
    if cfmmc_date.strip():
        st.session_state["cfmmc_trade_date"] = cfmmc_date.strip()
    with st.spinner("正在登录监控中心、下载逐日盯市结算单…"):
        try:
            r = requests.post(
                f"{api_base()}/api/v1/settlement/cfmmc-sync",
                json=payload,
                timeout=180,
            )
            if not r.ok:
                st.error(f"同步失败 HTTP {r.status_code}: {r.text[:500]}")
            else:
                resp = r.json()
                if resp.get("skipped"):
                    st.info(resp.get("message") or "已跳过")
                else:
                    st.success(
                        f"已导入昨仓 · import_id={resp.get('import_id')} · "
                        f"结算日 {resp.get('settlement_date')} · 持仓 {resp.get('position_count')} 条 · "
                        f"{resp.get('by_type_label', '逐日盯市')}"
                    )
                st.json(resp)
                if resp.get("suggested_session_date"):
                    st.session_state["session_date"] = resp["suggested_session_date"]
                if resp.get("account_id"):
                    st.session_state["account_id"] = resp["account_id"]
        except Exception as exc:  # noqa: BLE001
            st.error(f"请求失败（请确认 API 已启动 {api_base()}）: {exc}")

st.divider()
st.subheader("或手动上传结算单")

uploaded = st.file_uploader("选择结算单 (.xls)", type=["xls", "xlsx"])

col_a, col_b = st.columns([1, 1])
with col_a:
    preview_only = st.checkbox("仅本地预览（不入库）", value=False)
with col_b:
    do_upload = st.button("确认导入到系统", disabled=uploaded is None)

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
            st.dataframe(
                pd.DataFrame([t.to_dict() for t in parsed.option_trades]),
                use_container_width=True,
            )
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
