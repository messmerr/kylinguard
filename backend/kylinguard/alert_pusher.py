"""告警推送：Webhook（urllib，无额外依赖）和 Email（smtplib）。

所有 IO 在线程池中执行，对外暴露 async 接口，不阻塞事件循环。
"""
import asyncio
import email.mime.text
import json
import smtplib
import ssl
import urllib.error
import urllib.request
from dataclasses import asdict

from kylinguard.alert_rules import AlertChannel


def _push_webhook_sync(url: str, method: str, headers: dict, payload: dict) -> tuple[bool, str]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method=method.upper(),
        headers={"Content-Type": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status < 300, f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code} {e.reason}"
    except Exception as e:
        return False, str(e)


def _push_email_sync(host: str, port: int, user: str, password: str,
                     to: str, subject: str, body: str, use_tls: bool) -> tuple[bool, str]:
    msg = email.mime.text.MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to
    try:
        if use_tls:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, context=ctx, timeout=10) as s:
                s.login(user, password)
                s.sendmail(user, [to], msg.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=10) as s:
                s.starttls()
                s.login(user, password)
                s.sendmail(user, [to], msg.as_string())
        return True, "ok"
    except Exception as e:
        return False, str(e)


async def push_channel(channel: AlertChannel, alert_payload: dict) -> tuple[bool, str]:
    """向单个渠道推送告警，返回 (成功, 说明)。"""
    loop = asyncio.get_event_loop()
    cfg = channel.config

    if channel.type == "webhook":
        url = cfg.get("url", "")
        if not url:
            return False, "webhook url 未配置"
        method = cfg.get("method", "POST")
        headers = cfg.get("headers", {})
        return await loop.run_in_executor(
            None, _push_webhook_sync, url, method, headers, alert_payload)

    elif channel.type == "email":
        host = cfg.get("host", "")
        if not host:
            return False, "SMTP host 未配置"
        subject = f"【KylinGuard告警】{alert_payload.get('title', '系统告警')}"
        body = (
            f"规则：{alert_payload.get('rule_name', '-')}\n"
            f"指标：{alert_payload.get('metric', '-')}  值：{alert_payload.get('metric_value', '-')}\n"
            f"严重度：{alert_payload.get('severity', '-')}\n\n"
            f"{alert_payload.get('message', '')}"
        )
        return await loop.run_in_executor(
            None, _push_email_sync,
            host, int(cfg.get("port", 465)),
            cfg.get("user", ""), cfg.get("password", ""),
            cfg.get("to", ""), subject, body,
            cfg.get("use_tls", True),
        )

    return False, f"未知渠道类型: {channel.type}"


async def push_all(channels: list[AlertChannel], payload: dict) -> list[str]:
    """向所有启用渠道推送，返回成功的渠道名列表。"""
    results = await asyncio.gather(
        *(push_channel(ch, payload) for ch in channels if ch.enabled),
        return_exceptions=True,
    )
    notified = []
    for ch, res in zip([c for c in channels if c.enabled], results):
        if isinstance(res, Exception):
            continue
        ok, _ = res
        if ok:
            notified.append(ch.name)
    return notified
