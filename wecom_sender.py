# wecom_sender.py
import os
import json
import time
from typing import Optional

import requests


def _post_json(url: str, payload: dict, timeout: int = 15) -> dict:
    r = requests.post(url, json=payload, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    return data


def send_wecom_markdown(content: str, webhook_url: Optional[str] = None, retries: int = 3) -> None:
    """
    Send a markdown message to WeCom group bot webhook.
    Raises RuntimeError on failure.
    """
    webhook_url = webhook_url or os.getenv("WECOM_WEBHOOK_URL", "").strip()
    if not webhook_url:
        raise RuntimeError("Missing WECOM_WEBHOOK_URL (GitHub Secret).")

    payload = {
        "msgtype": "markdown",
        "markdown": {"content": content},
    }

    last_err = None
    for i in range(retries):
        try:
            data = _post_json(webhook_url, payload)
            # WeCom returns: {"errcode":0,"errmsg":"ok"} on success
            if data.get("errcode") == 0:
                return
            last_err = RuntimeError(f"WeCom webhook error: {json.dumps(data, ensure_ascii=False)}")
        except Exception as e:
            last_err = e

        # backoff
        time.sleep(1.2 * (i + 1))

    raise RuntimeError(f"Failed to send WeCom message after {retries} retries: {last_err}")


def build_sneaker_markdown(
    shoe_name: str,
    url: str,
    release_date: str = "未知",
    price: str = "未知",
    style_code: str = "未知",
    change_summary: str = "（无）",
) -> str:
    """
    A clean, stable template for sneaker updates (markdown).
    """
    # WeCom markdown supports basic markdown + <font color="..."> tags.
    return (
        f"**🆕 鞋款更新**\n\n"
        f"**{shoe_name}**\n\n"
        f"- 发售日期：`{release_date}`\n"
        f"- 价格：`{price}`\n"
        f"- 货号：`{style_code}`\n"
        f"- 原文：{url}\n\n"
        f"**变更摘要**\n"
        f"> {change_summary}\n"
    )
