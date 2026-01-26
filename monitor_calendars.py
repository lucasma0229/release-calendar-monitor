import os
import re
import json
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup


STATE_PATH = Path("state.json")
TZ_TAIPEI = timezone(timedelta(hours=8))

# 你要监控的页面（页面里通常包含很多鞋）
SITES = [
    {"name": "SBD | Air Jordan Release Dates 2026", "url": "https://sneakerbardetroit.com/air-jordan-release-dates/"},
    {"name": "SBD | Sneaker Release Dates 2026", "url": "https://sneakerbardetroit.com/sneaker-release-dates/"},
    {"name": "SneakerNews | Jordan Release Date Calendar 2026", "url": "https://sneakernews.com/air-jordan-release-dates/"},
    {"name": "SneakerNews | Sneaker Release Dates Calendar", "url": "https://sneakernews.com/release-dates/"},
]

# 噪音过滤（可按需扩充）
NOISE_KEYWORDS = [
    "advertisement", "sponsored", "cookie", "privacy", "subscribe",
    "twitter", "instagram", "facebook", "tiktok", "youtube", "pinterest",
    "sign up", "log in", "login", "newsletter", "share", "follow",
    "buy now", "shop now", "where to buy", "stockx", "goat", "ebay",
    "comments", "related posts", "recommended",
    "评论", "隐私", "条款", "订阅", "关注", "分享", "登录", "注册", "广告",
]

# 字段识别（A 模式）
PRICE_RE = re.compile(r"(\$|USD\s*)\s?(\d{2,4})(?:\.\d{2})?", re.I)
SKU_RE = re.compile(r"(style\s*code|sku)\s*[:：]?\s*([A-Z0-9\-]{6,})", re.I)
DATE_RE = re.compile(
    r"("
    r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+\d{1,2}(?:st|nd|rd|th)?[,]?\s+\d{4}"
    r"|"
    r"\d{4}[-/]\d{1,2}[-/]\d{1,2}"
    r")",
    re.I,
)

def now_taipei() -> str:
    return datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M")

def today_taipei() -> str:
    return datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d")

def norm_space(s: str) -> str:
    return " ".join((s or "").strip().split())

def looks_noisy(s: str) -> bool:
    low = (s or "").lower()
    return any(k in low for k in NOISE_KEYWORDS)

def safe_get(d: dict, *keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur

def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    # v3：sites -> 每个页面；shoes -> 每个页面下每双鞋；notified -> 去重
    return {"sites": {}, "shoes": {}, "notified": {}}

def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

def fetch_html(url: str) -> str:
    r = requests.get(url, timeout=30, headers={"User-Agent": "ReleaseMonitor/3.0"})
    r.raise_for_status()
    return r.text

def soup_from_html(html: str) -> BeautifulSoup:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup

def text_lines_from_soup(soup: BeautifulSoup) -> List[str]:
    text = soup.get_text("\n")
    lines = []
    for raw in text.splitlines():
        s = norm_space(raw)
        if not s:
            continue
        if looks_noisy(s):
            continue
        if len(s) > 200:
            continue
        lines.append(s)
    return lines

def stable_shoe_id(name: str, url: str) -> str:
    base = f"{norm_space(name).lower()}::{(url or '').strip()}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]

def hash_fields(fields: dict) -> str:
    payload = json.dumps(fields, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

def extract_candidate_shoes(
    site_url: str,
    soup: BeautifulSoup,
    detail_mode: bool,
) -> List[dict]:
    """
    以“稳”为第一原则的通用提取器：
    - 先把页面转成 text lines
    - 用窗口法寻找：像鞋名的一行 + 附近出现日期/价格/sku
    - url 尽量从页面内 <a> 的链接里找（同域/相对链接会拼接）
    这是“通用启发式”，不绑定单站点结构；后续我们可按站点定制提高准确度。
    """
    lines = text_lines_from_soup(soup)

    # 收集页面内所有链接文本映射（用来给鞋名找 URL）
    link_map: List[Tuple[str, str]] = []
    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        txt = norm_space(a.get_text(" "))
        if not href or not txt:
            continue
        if looks_noisy(txt):
            continue
        # 过滤明显非内容链接
        if txt.lower() in ("home", "next", "prev", "previous", "about", "contact"):
            continue
        link_map.append((txt, href))

    def resolve_url(href: str) -> str:
        if href.startswith("http://") or href.startswith("https://"):
            return href
        # 相对路径拼接（简单处理）
        if href.startswith("/"):
            m = re.match(r"^(https?://[^/]+)", site_url)
            if m:
                return m.group(1) + href
        return href

    def best_url_for_name(name: str) -> str:
        # 先做严格匹配：链接文本包含鞋名（或鞋名包含链接文本）
        n = norm_space(name)
        n_low = n.lower()
        best = ""
        best_score = 0
        for txt, href in link_map:
            t = txt.strip()
            t_low = t.lower()
            # 评分：重合度高优先
            score = 0
            if t_low == n_low:
                score = 100
            elif t_low in n_low or n_low in t_low:
                score = 60
            elif len(set(t_low.split()) & set(n_low.split())) >= 2:
                score = 30
            if score > best_score:
                best_score = score
                best = resolve_url(href)
        return best

    def is_probable_name(s: str) -> bool:
        s = norm_space(s)
        if len(s) < 8:
            return False
        if looks_noisy(s):
            return False
        if s.lower().startswith("http"):
            return False
        # 排除纯字段行
        if SKU_RE.search(s) or PRICE_RE.search(s) or DATE_RE.search(s):
            # 纯字段行通常不当作鞋名，但如果同时又包含多个单词也可能是鞋名里带价格/日期，这里保守排除
            return False
        # 排除很像按钮/栏目
        bad_starts = ("release date", "retail price", "style code", "sku", "updated", "update")
        if any(s.lower().startswith(b) for b in bad_starts):
            return False
        # 排除过短、全大写栏目
        if len(s) <= 10 and s.isupper():
            return False
        return True

    shoes: List[dict] = []
    seen_ids = set()

    # 窗口扫描：遇到可能鞋名 -> 在后续 N 行内找日期/价格/sku
    WINDOW = 12
    for i, line in enumerate(lines):
        if not is_probable_name(line):
            continue

        name = line
        release_date = ""
        price = ""
        sku = ""

        snippet_lines: List[str] = []
        for j in range(i, min(i + WINDOW, len(lines))):
            s = lines[j]
            snippet_lines.append(s)

            if not release_date:
                m = DATE_RE.search(s)
                if m:
                    release_date = norm_space(m.group(0))
            if not price:
                m = PRICE_RE.search(s)
                if m:
                    price = f"${m.group(2)}"
            if not sku:
                m = SKU_RE.search(s)
                if m:
                    sku = m.group(2).strip()

        url = best_url_for_name(name)

        # 过滤太“空”的候选：至少要命中日期或价格之一，否则容易误报
        if not release_date and not price and not sku:
            continue

        # A 模式字段
        fields = {
            "name": norm_space(name),
            "release_date": release_date,
            "price": price,
            "sku": sku,
            "url": url,
            "source_page": site_url,
        }

        # B 模式额外内容：把附近摘要加入 hash（更敏感）
        if detail_mode:
            # 摘要做更强降噪，避免过多 UI 文案
            snippet = [x for x in snippet_lines if not looks_noisy(x)]
            fields["detail_snippet"] = "\n".join(snippet[:20])

        sid = stable_shoe_id(fields["name"], fields["url"])
        if sid in seen_ids:
            continue
        seen_ids.add(sid)

        shoes.append({"id": sid, "fields": fields})

    return shoes

def build_item_line(fields: dict) -> str:
    # 你想要的格式：鞋名 + 发售时间 + 售价 + 网址
    name = fields.get("name") or "Unknown"
    date = fields.get("release_date") or "TBD"
    price = fields.get("price") or "TBD"
    url = fields.get("url") or fields.get("source_page") or ""
    return f"{name} | {date} | {price}\n{url}"

def diff_fields(old: dict, new: dict) -> List[str]:
    keys = ["name", "release_date", "price", "sku", "url"]
    changes = []
    for k in keys:
        if (old.get(k) or "") != (new.get(k) or ""):
            changes.append(f"- {k}: {old.get(k,'') or '∅'} → {new.get(k,'') or '∅'}")
    return changes

# -------- GitHub Issue 评论（触发邮件） --------
def gh_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ReleaseMonitor/3.0",
    }

def gh_comment_issue(repo: str, token: str, issue_number: int, body: str) -> None:
    url = f"https://api.github.com/repos/{repo}/issues/{issue_number}/comments"
    r = requests.post(url, headers=gh_headers(token), json={"body": body}, timeout=30)
    r.raise_for_status()

# -------- Telegram 通知（可选） --------
def tg_send(bot_token: str, chat_id: str, text: str) -> None:
    if not bot_token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    r = requests.post(url, json=payload, timeout=30)
    r.raise_for_status()

def event_id_for(site_url: str, shoe_id: str, new_hash: str) -> str:
    raw = f"{site_url}::{shoe_id}::{new_hash}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def build_comment_body(
    site_name: str,
    site_url: str,
    created: List[dict],
    updated: List[Tuple[dict, dict, List[str]]],
    removed: List[dict],
    detail_mode: bool,
) -> str:
    lines: List[str] = []
    lines.append("🔔 **Sneaker-level Update Detected (V3)**")
    lines.append(f"- **来源页面**：{site_name}")
    lines.append(f"- **页面链接**：{site_url}")
    lines.append(f"- **检测时间（台北）**：{now_taipei()}")
    lines.append(f"- **模式**：{'B(更敏感)' if detail_mode else 'A(字段级)'}")
    lines.append("")
    lines.append("---")
    lines.append("")

    if created:
        lines.append("## ✅ 新增鞋款")
        for it in created[:40]:
            lines.append(f"- {build_item_line(it['fields']).replace(chr(10), '  ')}")
        lines.append("")

    if updated:
        lines.append("## 🔁 鞋款字段更新")
        for old_it, new_it, changes in updated[:60]:
            f = new_it["fields"]
            lines.append(f"### {f.get('name','Unknown')}")
            lines.append(build_item_line(f))
            if changes:
                lines.append("")
                lines.extend(changes)
            lines.append("")
        lines.append("")

    if removed:
        lines.append("## 🗑️ 页面移除/不可见（谨慎参考）")
        for it in removed[:20]:
            lines.append(f"- {build_item_line(it['fields']).replace(chr(10), '  ')}")
        lines.append("")

    return "\n".join(lines).strip()

def build_tg_text(
    site_name: str,
    created: List[dict],
    updated: List[Tuple[dict, dict, List[str]]],
    detail_mode: bool,
) -> str:
    # Telegram 更短：只发关键条目（你要的四要素）
    parts: List[str] = []
    parts.append(f"🔥 Sneaker Update (V3) | {site_name}")
    parts.append(f"Mode: {'B' if detail_mode else 'A'} | {now_taipei()}")
    parts.append("")
    count = 0

    def add_item(prefix: str, fields: dict):
        nonlocal count
        if count >= 12:
            return
        parts.append(f"{prefix} {fields.get('name','Unknown')} | {fields.get('release_date') or 'TBD'} | {fields.get('price') or 'TBD'}")
        parts.append(fields.get("url") or fields.get("source_page") or "")
        parts.append("")
        count += 1

    for it in created:
        add_item("✅ NEW:", it["fields"])
    for old_it, new_it, _changes in updated:
        add_item("🔁 UPD:", new_it["fields"])

    if count == 0:
        parts.append("No actionable changes.")

    return "\n".join(parts).strip()

def main():
    token = os.getenv("GITHUB_TOKEN", "").strip()
    repo = os.getenv("GITHUB_REPOSITORY", "").strip()
    issue_number = os.getenv("ISSUE_NUMBER", "").strip()

    detail_mode = os.getenv("DETAIL_MODE", "").strip() == "1"  # 0=A(默认), 1=B(更敏感)

    tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    tg_chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    if not SITES:
        print("[WARN] SITES 为空，请在 monitor_calendars.py 顶部填入要监控的网址。")
        return

    state = load_state()
    today = today_taipei()

    # notified[date] 存 event_id 列表，防止同一天同一变化刷屏
    state["notified"].setdefault(today, [])

    for site in SITES:
        site_name = site["name"]
        site_url = site["url"]

        print(f"\n[CHECK] {site_name} | {site_url}")
        html = fetch_html(site_url)
        soup = soup_from_html(html)

        current = extract_candidate_shoes(site_url, soup, detail_mode=detail_mode)

        # 读取历史
        prev_shoes: Dict[str, dict] = safe_get(state, "shoes", site_url, default={}) or {}
        cur_map: Dict[str, dict] = {it["id"]: it for it in current}

        created: List[dict] = []
        removed: List[dict] = []
        updated: List[Tuple[dict, dict, List[str]]] = []

        # 新增/更新
        for sid, cur_it in cur_map.items():
            cur_fields = cur_it["fields"]
            cur_hash = hash_fields(cur_fields)

            old_it = prev_shoes.get(sid)
            if not old_it:
                created.append(cur_it)
                continue

            old_fields = old_it.get("fields", {})
            old_hash = old_it.get("hash", "")

            if old_hash != cur_hash:
                # A 模式：只比较字段变化（我们 hash 里已是字段；B 模式 hash 里包含 snippet）
                changes = diff_fields(old_fields, cur_fields)
                updated.append((old_it, cur_it, changes))

        # 移除（谨慎：页面结构变动会导致“看不到”，所以只做参考）
        for sid, old_it in prev_shoes.items():
            if sid not in cur_map:
                removed.append(old_it)

        # 如果没有“可行动变化”，更新 state 后跳过通知
        actionable = bool(created or updated)
        if not actionable:
            print("[OK] No actionable shoe-level change.")
            # 仍然更新鞋库（比如因噪音导致小波动，我们也尽量稳定）
            state.setdefault("shoes", {}).setdefault(site_url, {})
            state["shoes"][site_url] = {
                sid: {"hash": hash_fields(it["fields"]), "fields": it["fields"]}
                for sid, it in cur_map.items()
            }
            continue

        # 通知去重：对每个变化鞋生成 event_id，已通知则跳过
        # 这里按“站点一次通知”合并成一个 comment + 一条 TG（避免太吵）
        event_ids = []
        for it in created:
            event_ids.append(event_id_for(site_url, it["id"], hash_fields(it["fields"])))
        for _old_it, new_it, _changes in updated:
            event_ids.append(event_id_for(site_url, new_it["id"], hash_fields(new_it["fields"])))

        if all(eid in state["notified"][today] for eid in event_ids):
            print("[SKIP] Duplicate events, already notified today.")
        else:
            comment_body = build_comment_body(
                site_name, site_url, created, updated, removed, detail_mode=detail_mode
            )

            # GitHub Issue 评论（邮件来源）
            if token and repo and issue_number:
                print(f"[ALERT] Posting comment to fixed issue #{issue_number} ...")
                gh_comment_issue(repo, token, int(issue_number), comment_body)
                print("[OK] Comment added.")
            else:
                print("[WARN] Missing GITHUB_TOKEN / GITHUB_REPOSITORY / ISSUE_NUMBER, skip Issue comment.")

            # Telegram
            if tg_token and tg_chat_id:
                tg_text = build_tg_text(site_name, created, updated, detail_mode=detail_mode)
                try:
                    tg_send(tg_token, tg_chat_id, tg_text)
                    print("[OK] Telegram sent.")
                except Exception as e:
                    print(f"[WARN] Telegram send failed: {e}")

            # 写入已通知列表
            if "notified" not in state or not isinstance(state["notified"], dict):state["notified"] = {}
                
            if today not in state["notified"] or not isinstance(state["notified"][today], list):state["notified"][today] = []
                
            # 写入已通知的事件ID
            for eid in event_ids:
                if eid not in state["notified"][today]:
                    state["notified"][today].append(eid)

        # 更新鞋库 state
        state.setdefault("shoes", {}).setdefault(site_url, {})
        state["shoes"][site_url] = {
            sid: {"hash": hash_fields(it["fields"]), "fields": it["fields"]}
            for sid, it in cur_map.items()
        }

    save_state(state)
    print("\n[DONE] State saved.")


if __name__ == "__main__":
    main()
