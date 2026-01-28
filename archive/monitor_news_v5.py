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
from bs4 import BeautifulSoup, Tag

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
# 你提供的 4 个来源（已填好）
# type 用来选择解析器：优先结构化解析，失败则自动fallback
# =============================
SITES = [
    {
        "name": "SBD | Air Jordan Release Dates",
        "url": "https://sneakerbardetroit.com/air-jordan-release-dates/",
        "type": "sbd_calendar",
        "tag": "Jordan",
    },
    {
        "name": "SBD | Sneaker Release Dates",
        "url": "https://sneakerbardetroit.com/sneaker-release-dates/",
        "type": "sbd_calendar",
        "tag": "Other",
    },
    {
        "name": "SneakerNews | Air Jordan Release Dates",
        "url": "https://sneakernews.com/air-jordan-release-dates/",
        "type": "sneakernews_calendar",
        "tag": "Jordan",
    },
    {
        "name": "SneakerNews | Release Dates",
        "url": "https://sneakernews.com/release-dates/",
        "type": "sneakernews_calendar",
        "tag": "Other",
    },
]

# =============================
# 通知渠道
# =============================
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY", "")
ISSUE_NUMBER = os.getenv("ISSUE_NUMBER", "")  # 可选：固定写入哪个 issue

# 兼容你原来的 secrets 名称（在 workflow 里做映射也行）
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "")

WECOM_WEBHOOK = os.getenv("WECOM_WEBHOOK", "")

# =============================
# 正则：SKU / 价格 / 日期
# =============================
SKU_RE = re.compile(r"\b([A-Z]{1,3}\d{3,6}[-/]\d{3})\b", re.I)
PRICE_RE = re.compile(r"(\$|USD\s*)\s*(\d{2,4})\b", re.I)

MONTH_RE = r"(January|February|March|April|May|June|July|August|September|October|November|December)"
MONTH_HEADER_RE = re.compile(rf"^{MONTH_RE}\s+\d{{4}}$", re.I)
FULL_DATE_RE = re.compile(rf"^{MONTH_RE}\s+\d{{1,2}},\s*\d{{4}}$", re.I)
WEEKDAY_PREFIX_RE = re.compile(r"^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s*,\s*", re.I)

# 更宽松的“日期上下文”识别：例如 "Feb 7", "February 07", "Spring 2026", "Holiday 2025", "TBA"
LOOSE_DATE_RE = re.compile(
    r"(?i)\b("
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?\s*\d{1,2}(?:,\s*\d{4})?"
    r"|"
    rf"{MONTH_RE}\s+\d{{1,2}}(?:,\s*\d{{4}})?"
    r"|"
    r"(Spring|Summer|Fall|Autumn|Holiday|Winter)\s*\d{4}"
    r"|"
    r"(Early|Late)\s*\d{4}"
    r"|"
    r"(Q[1-4])\s*\d{4}"
    r"|"
    r"(TBA|TB D|To Be Announced)"
    r")\b"
)

NOISE_CONTAINS = [
    "sign in", "forgot your password", "recover", "password",
    "your username", "your email", "privacy policy", "terms of use",
    "advertisement", "cookie"
]

# =============================
# 数据结构
# =============================
@dataclass
class ReleaseItem:
    name: str
    date: str
    sku: str
    price: str
    tag: str
    source_name: str
    source_url: str

    def key(self) -> str:
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


def normalize_text(s: str) -> str:
    s = re.sub(r"\s+", " ", (s or "")).strip()
    return s


def looks_like_noise(line: str) -> bool:
    low = (line or "").lower()
    return any(n in low for n in NOISE_CONTAINS)


def clean_name(text: str) -> str:
    # 移除 sku/price 等
    t = SKU_RE.sub("", text)
    t = PRICE_RE.sub("", t)
    t = normalize_text(t).strip(" -|•:·")
    return t


def pick_main_container(soup: BeautifulSoup) -> Tag:
    return (
        soup.select_one("article")
        or soup.select_one("main")
        or soup.select_one(".entry-content")
        or soup.select_one(".post-content")
        or soup.select_one(".content")
        or soup.body
    )


# =============================
# 兜底解析器（只认“含 SKU 的行”为鞋款，日期作为上下文）
# =============================
def parse_fallback_text_sku(site: dict, html: str) -> List[ReleaseItem]:
    soup = BeautifulSoup(html, "html.parser")
    main = pick_main_container(soup)

    for tag in main.select("script, style, noscript"):
        tag.decompose()

    text = main.get_text("\n", strip=True)
    lines: List[str] = []
    for ln in text.splitlines():
        ln = normalize_text(ln)
        if not ln or looks_like_noise(ln):
            continue
        lines.append(ln)

    items: List[ReleaseItem] = []
    current_date = "未知"

    for ln in lines:
        # 日期上下文（不作为条目）
        if MONTH_HEADER_RE.match(ln):
            current_date = ln
            continue
        if FULL_DATE_RE.match(ln):
            current_date = ln
            continue
        if WEEKDAY_PREFIX_RE.search(ln):
            maybe = WEEKDAY_PREFIX_RE.sub("", ln)
            if FULL_DATE_RE.match(maybe):
                current_date = maybe
                continue
        # 宽松日期：比如 "February 2026"、"Feb 7"、"Spring 2026"…
        m_date = LOOSE_DATE_RE.search(ln)
        if m_date and len(ln) <= 40 and not SKU_RE.search(ln):
            current_date = normalize_text(m_date.group(0))
            continue

        sku_m = SKU_RE.search(ln)
        if not sku_m:
            continue

        sku = sku_m.group(1).upper().replace("/", "-")
        price_m = PRICE_RE.search(ln)
        price = f"${price_m.group(2)}" if price_m else "未知"
        name = clean_name(ln)
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

    uniq: Dict[str, ReleaseItem] = {}
    for it in items:
        uniq[it.key()] = it
    return list(uniq.values())


# =============================
# 结构化解析：SBD（日历页通常是 标题(h2/h3)+列表(ul/li) + 段落）
# 思路：
# - 在正文里按 DOM 顺序遍历：h2/h3/h4 更新日期上下文；li/p 中提取条目
# - 条目优先：含 SKU 的 li/p
# - 如果某条没有 SKU，但含明显鞋名并且附近（同段/同li）有 SKU，则合并
# =============================
def parse_sbd_calendar(site: dict, html: str) -> List[ReleaseItem]:
    soup = BeautifulSoup(html, "html.parser")
    main = pick_main_container(soup)

    for tag in main.select("script, style, noscript"):
        tag.decompose()

    current_date = "未知"
    items: List[ReleaseItem] = []

    # 遍历正文内常见元素
    elements = main.select("h2, h3, h4, li, p")
    for el in elements:
        txt = normalize_text(el.get_text(" ", strip=True))
        if not txt or looks_like_noise(txt):
            continue

        # 标题更新日期上下文
        if el.name in ("h2", "h3", "h4"):
            # 去掉星期
            t2 = WEEKDAY_PREFIX_RE.sub("", txt)
            if MONTH_HEADER_RE.match(t2) or FULL_DATE_RE.match(t2):
                current_date = t2
                continue
            # 宽松日期（标题常用）
            md = LOOSE_DATE_RE.search(t2)
            if md and len(t2) <= 50 and not SKU_RE.search(t2):
                current_date = normalize_text(md.group(0))
                continue
            continue

        # li / p 当作候选条目（SBD 常在 li 里）
        sku_m = SKU_RE.search(txt)
        if not sku_m:
            # 没 SKU 的行可能是“鞋名 + 日期”，但为了防止误报，这里不直接当新增
            continue

        sku = sku_m.group(1).upper().replace("/", "-")
        price_m = PRICE_RE.search(txt)
        price = f"${price_m.group(2)}" if price_m else "未知"
        name = clean_name(txt)

        items.append(
            ReleaseItem(
                name=name or txt,
                date=current_date,
                sku=sku,
                price=price,
                tag=site.get("tag", "Other"),
                source_name=site["name"],
                source_url=site["url"],
            )
        )

    # 如果结构化抓到的条目太少，fallback（避免站点改版导致漏抓）
    uniq: Dict[str, ReleaseItem] = {it.key(): it for it in items}
    if len(uniq) < 3:
        return parse_fallback_text_sku(site, html)

    return list(uniq.values())


# =============================
# 结构化解析：SneakerNews（日历页常见：月份/日期标题 + 列表项）
# 逻辑与 SBD 类似，但 SneakerNews 有时在段落或 list 里更分散，所以也遍历 a/li/p
# =============================
def parse_sneakernews_calendar(site: dict, html: str) -> List[ReleaseItem]:
    soup = BeautifulSoup(html, "html.parser")
    main = pick_main_container(soup)

    for tag in main.select("script, style, noscript"):
        tag.decompose()

    current_date = "未知"
    items: List[ReleaseItem] = []

    elements = main.select("h2, h3, h4, li, p, a")
    for el in elements:
        txt = normalize_text(el.get_text(" ", strip=True))
        if not txt or looks_like_noise(txt):
            continue

        if el.name in ("h2", "h3", "h4"):
            t2 = WEEKDAY_PREFIX_RE.sub("", txt)
            if MONTH_HEADER_RE.match(t2) or FULL_DATE_RE.match(t2):
                current_date = t2
                continue
            md = LOOSE_DATE_RE.search(t2)
            if md and len(t2) <= 50 and not SKU_RE.search(t2):
                current_date = normalize_text(md.group(0))
                continue
            continue

        # 对 a 标签：有些鞋名在 a，SKU 在同一 li/p
        # 这里直接看文本是否含 SKU
        sku_m = SKU_RE.search(txt)
        if not sku_m:
            continue

        sku = sku_m.group(1).upper().replace("/", "-")
        price_m = PRICE_RE.search(txt)
        price = f"${price_m.group(2)}" if price_m else "未知"
        name = clean_name(txt)

        items.append(
            ReleaseItem(
                name=name or txt,
                date=current_date,
                sku=sku,
                price=price,
                tag=site.get("tag", "Other"),
                source_name=site["name"],
                source_url=site["url"],
            )
        )

    uniq: Dict[str, ReleaseItem] = {it.key(): it for it in items}
    if len(uniq) < 3:
        return parse_fallback_text_sku(site, html)
    return list(uniq.values())


# =============================
# 解析器注册表
# =============================
PARSERS: Dict[str, Callable[[dict, str], List[ReleaseItem]]] = {
    "sbd_calendar": parse_sbd_calendar,
    "sneakernews_calendar": parse_sneakernews_calendar,
    "fallback": parse_fallback_text_sku,
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
        "User-Agent": "release-monitor-v5",
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
    requests.post(
        url,
        json={"chat_id": TG_CHAT_ID, "text": text, "disable_web_page_preview": True},
        timeout=TIMEOUT,
    ).raise_for_status()


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

    tag = site.get("tag", "Other")

    if added:
        lines.append(f"🧩 {tag} / ✅ 新增鞋款")
        for it in added[:40]:
            lines.append(f"• {it.name} | {it.sku} | {it.date} | {it.price}")
        if len(added) > 40:
            lines.append(f"… 还有 {len(added)-40} 条未展示")
        lines.append("")

    if updated:
        lines.append(f"🧩 {tag} / 🔁 信息更新")
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
        parser = PARSERS.get(site.get("type", "fallback"), parse_fallback_text_sku)

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
