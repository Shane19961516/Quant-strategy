"""微信/多渠道推送封装。

说明：
- 个人微信无法仅凭手机号直接下发（需用户关注推送公众号并拿到 token）。
- 默认走 PushPlus 微信渠道；可选 Server酱 / 企业微信机器人 / 邮件式 webhook。
- 手机号仅用于消息抬头展示，或在 channel=sms 时作为 PushPlus 短信接收方绑定参考。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def push_message(title: str, content: str, content_type: str = "html") -> dict[str, Any]:
    """按环境变量选择渠道推送。返回各渠道结果。"""
    results: dict[str, Any] = {}
    sent = False

    pushplus_token = _env("PUSHPLUS_TOKEN")
    if pushplus_token:
        channel = _env("PUSHPLUS_CHANNEL", "wechat")  # wechat|mail|sms|cp|webhook
        results["pushplus"] = _pushplus(pushplus_token, title, content, channel, content_type)
        sent = True

    serverchan_key = _env("SERVERCHAN_SENDKEY")
    if serverchan_key:
        results["serverchan"] = _serverchan(serverchan_key, title, content)
        sent = True

    wecom_webhook = _env("WECOM_WEBHOOK")
    if wecom_webhook:
        results["wecom"] = _wecom_webhook(wecom_webhook, title, content)
        sent = True

    if not sent:
        results["error"] = (
            "未配置推送渠道。请设置环境变量 PUSHPLUS_TOKEN "
            "或 SERVERCHAN_SENDKEY 或 WECOM_WEBHOOK。"
        )
    return results


def _post_json(url: str, payload: dict, timeout: int = 20) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            try:
                return {"ok": True, "status": resp.status, "body": json.loads(body)}
            except json.JSONDecodeError:
                return {"ok": True, "status": resp.status, "body": body}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {"ok": False, "status": e.code, "body": body}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def _post_form(url: str, payload: dict, timeout: int = 20) -> dict[str, Any]:
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            try:
                return {"ok": True, "status": resp.status, "body": json.loads(body)}
            except json.JSONDecodeError:
                return {"ok": True, "status": resp.status, "body": body}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def _pushplus(token: str, title: str, content: str, channel: str, content_type: str) -> dict:
    # https://www.pushplus.plus/doc/guide/api.html
    template = "html" if content_type == "html" else "txt"
    payload = {
        "token": token,
        "title": title[:90],
        "content": content,
        "template": template,
        "channel": channel,
    }
    # 一对一默认即可；topic 用于一对多
    topic = _env("PUSHPLUS_TOPIC")
    if topic:
        payload["topic"] = topic
    return _post_json("https://www.pushplus.plus/send", payload)


def _serverchan(sendkey: str, title: str, content: str) -> dict:
    # https://sct.ftqq.com/
    url = f"https://sctapi.ftqq.com/{sendkey}.send"
    # Server酱 markdown
    return _post_form(url, {"title": title[:90], "desp": content})


def _wecom_webhook(webhook: str, title: str, content: str) -> dict:
    # 企业微信群机器人 markdown
    text = f"## {title}\n{content}"
    # 企业微信 markdown 不支持很多 HTML，转简易文本
    text = (
        text.replace("<br>", "\n")
        .replace("<br/>", "\n")
        .replace("<b>", "**")
        .replace("</b>", "**")
        .replace("<h3>", "### ")
        .replace("</h3>", "\n")
        .replace("<code>", "`")
        .replace("</code>", "`")
    )
    payload = {"msgtype": "markdown", "markdown": {"content": text[:3800]}}
    return _post_json(webhook, payload)
