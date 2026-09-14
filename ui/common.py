"""Shared helpers for Streamlit pages."""

from __future__ import annotations

import sys
from pathlib import Path

import requests
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def api_base() -> str:
    return st.session_state.get("api_base", "http://127.0.0.1:8000")


def account_id() -> str:
    return st.session_state.get("account_id", "166308")


def session_date() -> str:
    return st.session_state.get("session_date", "")


def inject_sidebar() -> None:
    st.sidebar.markdown("### 账户 / API")
    st.session_state["api_base"] = st.sidebar.text_input(
        "API Base", value=st.session_state.get("api_base", "http://127.0.0.1:8000")
    )
    st.session_state["account_id"] = st.sidebar.text_input(
        "资金账号", value=st.session_state.get("account_id", "166308")
    )
    # try load suggested session date
    try:
        r = requests.get(
            f"{api_base()}/api/v1/settlement/active",
            params={"account_id": account_id()},
            timeout=3,
        )
        if r.ok:
            data = r.json()
            suggested = data.get("suggested_session_date", "")
            st.sidebar.caption(
                f"结算日 {data.get('settlement_date')} · 权益 {data.get('client_equity'):,.0f}"
            )
            default_sess = st.session_state.get("session_date") or suggested
            st.session_state["session_date"] = st.sidebar.text_input("监控交易日", value=default_sess)
        else:
            st.session_state["session_date"] = st.sidebar.text_input(
                "监控交易日", value=st.session_state.get("session_date", "")
            )
            st.sidebar.warning("尚未导入结算单")
    except Exception:
        st.session_state["session_date"] = st.sidebar.text_input(
            "监控交易日", value=st.session_state.get("session_date", "")
        )
        st.sidebar.info("API 未连接时仍可本地解析预览")


def get_json(path: str, params: dict | None = None):
    r = requests.get(f"{api_base()}{path}", params=params or {}, timeout=30)
    r.raise_for_status()
    return r.json()


def post_json(path: str, payload: dict):
    r = requests.post(f"{api_base()}{path}", json=payload, timeout=30)
    r.raise_for_status()
    return r.json()


def post_file(path: str, file_bytes: bytes, filename: str, form: dict | None = None):
    files = {"file": (filename, file_bytes, "application/vnd.ms-excel")}
    data = form or {}
    r = requests.post(f"{api_base()}{path}", files=files, data=data, timeout=60)
    r.raise_for_status()
    return r.json()
