import os
import re
import json
import time
import hashlib
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Callable

import requests
from bs4 import BeautifulSoup

# =============================
# 基础配置
# =============================
STATE_PATH = Path("state.json")
TZ_TAIPEI = timezone(timedelta(hours=8))
TIMEOUT = 25

USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

# =============================
# 站点列表（你后续把URL补齐/替换）
# type 用来选择解析器
# =============================
SITES = [
    {
        "name": "SBD | Air Jordan Release Dates",
        "url": "https://sneakerbardetroit.com/air-jordan-release-dates/",
        "type": "calendar_text_sku",   # 先用通用解析器兜底，后面我会给你做“专用解析器”
        "tag": "Jordan",
    },
    # 下面先留空位，等你发真实URL
    # {"name": "SBD | Sneaker Release Dates 2026", "url": "...", "type": "calendar_text_sku", "tag": "General"},
    # {"name": "SneakerNews | Jordan Release Date Calendar 2026", "url": "...", "type": "calendar_text_sku", "tag": "Jordan"},
    # {"name": "SneakerNews | Sneaker Release Dates Calendar 2025", "url": "...", "type": "calendar_text_sku", "tag": "General"},
]

# =============================
# 通知渠道：GitHub Issue（邮件）、Telegram、企业微信
# =============================
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY", "")
ISSUE_NUMBER = os.getenv("ISSUE_NUMBER", "")  # 可选：固定写入哪个 issue

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "")

WECOM_WEBHOOK = os.getenv("WECOM_WEBHOOK", "")

# =============================
# 正则：SKU / 价格 / 日期（用于上下文，不作为“新增鞋款”）
# =============================
SKU_RE = re.compile(r"\b([A-Z]{1,3}\d{3,6}[-/]\d{3})\b", re.I)
PRICE_RE = re.compile(r"(\$|USD\s*)\s*(\d{2,4})\b", re.I)

MONTH_HEADER_RE = re.compile(
    r"^(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}$",
    re.I
)
FULL_DATE_RE = re.compile(
    r"^(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s*\d{4}$",
    re.I
)
WEEKDAY_PREFIX_RE = re.compile(r"^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s*,\s*", re.I)

NOISE_CONTAINS = [
    "sign in", "forgot your password", "recover", "password",
    "your username", "your email", "privacy policy", "terms of use"
]

# =============================
# 数据结构
# =============================
@dataclass
class ReleaseItem:
    name: str
    date: str          # 允许“February 2026”这种上下文日期
    sku: str
    price: str
    tag: str
    source_name: str
    source_url: str

    def key(self) -> str:
        """
        强主键：SKU；没有 SKU 时用 name+date+source_url hash。
        """
        sku = (self.sku or "").strip().upper().replace("/", "-")
        if sku:
            return f"SKU:{sku}"
        raw = f"{self.name}|{self.date}|{self.source_url}"
        return "HASH:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


# =============================
# 工具函数
# =============================
def now_taipei_str() -> str:
    return datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M")


def http_get(url: str) -> str:
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text


def extract_main_text_lines(html: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")

    main = (
        soup.select_one("article")
        or soup.select_one("main")
        or soup.select_one(".entry-content")
        or soup.select_one(".post-content")
        or soup.select_one(".content")
        or soup.body
    )

    for tag in main.select("script, style, noscript"):
        tag.decompose()

    text = main.get_text("\n", strip=True)

    lines: List[str] = []
    for ln in text.splitlines():
        ln = re.sub(r"\s+", " ", ln).strip()
        if not ln:
            continue
        low = ln.lower()
        if any(n in low for n in NOISE_CONTAINS):
            continue
        lines.append(ln)
    return lines


# =============================
# 通用兜底解析器（解决你现在“日期混进新增鞋款”的核心）
# 规则：只把“含 SKU 的行”当候选条目；日期行只更新上下文 current_date
# =============================
def parse_calendar_text_sku(site: dict, html: str) -> List[ReleaseItem]:
    lines = extract_main_text_lines(html)

    items: List[ReleaseItem] = []
    current_date = "未知"

    for ln in lines:
        # 日期上下文（不作为鞋款条目）
        if MONTH_HEADER_RE.match(ln):
            current_date = ln
            continue
        if FULL_DATE_RE.match(ln):
            current_date = ln
            continue
        if WEEKDAY_PREFIX_RE.search(ln) and FULL_DATE_RE.search(WEEKDAY_PREFIX_RE.sub("", ln)):
            current_date = WEEKDAY_PREFIX_RE.sub("", ln)
            continue

        # 只有含 SKU 才算“鞋款条目”
        sku_m = SKU_RE.search(ln)
        if not sku_m:
            continue

        sku = sku_m.group(1).upper().replace("/", "-")

        price_m = PRICE_RE.search(ln)
        price = f"${price_m.group(2)}" if price_m else "未知"

        # 名称：剔除 sku/price
        name = SKU_RE.sub("", ln)
        name = PRICE_RE.sub("", name)
        name = re.sub(r"\s{2,}", " ", name).strip(" -|•")

        if len(name) < 3:
            name = ln

        items.append(
            ReleaseItem(
                name=name,
                date=current_date,
                sku=sku,
                price=price,
                tag=site.get("tag", "Other"),
                source_name=site["name"],
                source_url=site["url"],
            )
        )

    # 去重
    uniq: Dict[str, ReleaseItem] = {}
    for it in items:
        uniq[it.key()] = it
    return list(uniq.values())


PARSERS: Dict[str, Callable[[dict, str], List[ReleaseItem]]] = {
    "calendar_text_sku": parse_calendar_text_sku,
}

# =============================
# 状态 & diff
# =============================
def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text("utf-8"))
    return {"sites": {}}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")


def diff_items(old_map: Dict[str, dict], new_items: List[ReleaseItem]) -> Tuple[List[ReleaseItem], List[Tuple[ReleaseItem, dict]]]:
    added: List[ReleaseItem] = []
    updated: List[Tuple[ReleaseItem, dict]] = []

    for it in new_items:
        k = it.key()
        if k not in old_map:
            added.append(it)
        else:
            prev = old_map[k]
            # 只关心字段变化（避免噪声）
            if prev.get("date") != it.date or prev.get("price") != it.price or prev.get("name") != it.name:
                updated.append((it, prev))

    return added, updated


# =============================
# 通知：GitHub Issue（触发邮件）、Telegram、企业微信
# =============================
def gh_api(method: str, url: str, token: str, json_body: Optional[dict] = None) -> dict:
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "release-monitor-v4",
    }
    r = requests.request(method, url, headers=headers, json=json_body, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json() if r.text else {}


def ensure_issue() -> Optional[int]:
    if not (GITHUB_TOKEN and GITHUB_REPOSITORY):
        return None

    if ISSUE_NUMBER:
        try:
            return int(ISSUE_NUMBER)
        except:
            return None

    data = gh_api(
        "POST",
        f"https://api.github.com/repos/{GITHUB_REPOSITORY}/issues",
        GITHUB_TOKEN,
        {"title": "🔥 Sneaker Release Monitor Log", "body": "Auto log for release updates."},
    )
    return data.get("number")


def post_issue_comment(issue_number: int, comment: str) -> None:
    if not (GITHUB_TOKEN and GITHUB_REPOSITORY and issue_number):
        return
    gh_api(
        "POST",
        f"https://api.github.com/repos/{GITHUB_REPOSITORY}/issues/{issue_number}/comments",
        GITHUB_TOKEN,
        {"body": comment},
    )


def send_telegram(text: str) -> None:
    if not (TG_BOT_TOKEN and TG_CHAT_ID):
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TG_CHAT_ID, "text": text, "disable_web_page_preview": True}, timeout=TIMEOUT).raise_for_status()


def send_wecom(text: str) -> None:
    if not WECOM_WEBHOOK:
        return
    payload = {"msgtype": "text", "text": {"content": text}}
    requests.post(WECOM_WEBHOOK, json=payload, timeout=TIMEOUT).raise_for_status()


def format_push(site: dict, added: List[ReleaseItem], updated: List[Tuple[ReleaseItem, dict]]) -> str:
    lines: List[str] = []
    lines.append("🔔 Sneaker Release Update Detected")
    lines.append(f"• 来源：{site['name']}")
    lines.append(f"• 原文链接：{site['url']}")
    lines.append(f"• 检测时间（台北）：{now_taipei_str()}")
    lines.append("")

    if added:
        lines.append(f"🧩 {site.get('tag','Other')} / ✅ 新增鞋款")
        for it in added[:40]:
            lines.append(f"• {it.name} | {it.sku} | {it.date} | {it.price}")
        if len(added) > 40:
            lines.append(f"… 还有 {len(added)-40} 条未展示")
        lines.append("")

    if updated:
        lines.append(f"🧩 {site.get('tag','Other')} / 🔁 信息更新")
        for it, prev in updated[:40]:
            diffs = []
            if prev.get("date") != it.date:
                diffs.append(f"日期：{prev.get('date')} → {it.date}")
            if prev.get("price") != it.price:
                diffs.append(f"价格：{prev.get('price')} → {it.price}")
            if prev.get("name") != it.name:
                diffs.append("名称更新")
            lines.append(f"• {it.name} | {it.sku} | {'；'.join(diffs) if diffs else '信息变化'}")
        if len(updated) > 40:
            lines.append(f"… 还有 {len(updated)-40} 条未展示")
        lines.append("")

    return "\n".join(lines).strip()


def main():
    state = load_state()
    state.setdefault("sites", {})

    issue_no = ensure_issue()

    any_change = False

    for site in SITES:
        url = site["url"]
        parser_type = site.get("type", "")
        parser = PARSERS.get(parser_type)

        if not parser:
            print(f"[Skip] No parser for type={parser_type} | {site['name']}")
            continue

        print(f"[Fetch] {site['name']} -> {url}")
        try:
            html = http_get(url)
        except Exception as e:
            msg = f"⚠️ 抓取失败\n• 来源：{site['name']}\n• 链接：{url}\n• 错误：{e}"
            print(msg)
            if issue_no:
                post_issue_comment(issue_no, msg)
            continue

        new_items = parser(site, html)
        old_map = state["sites"].get(url, {}).get("items", {})

        added, updated = diff_items(old_map, new_items)

        if added or updated:
            any_change = True
            push_text = format_push(site, added, updated)

            if issue_no:
                post_issue_comment(issue_no, push_text)
            send_telegram(push_text)
            send_wecom(push_text)

        # 更新状态
        state["sites"][url] = {
            "last_checked": now_taipei_str(),
            "count": len(new_items),
            "items": {it.key(): asdict(it) for it in new_items},
        }

        time.sleep(1)

    save_state(state)
    print("Done:", "changes detected." if any_change else "no changes.")


if __name__ == "__main__":
    main()
