import os
import re
import json
import time
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Tuple, Optional

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
    # shoes：每个页面下每双鞋；notified：去重（按 day + site_name）
    return {"shoes": {}, "notified": {}}


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
        if txt.lower() in ("home", "next", "prev", "previous", "about", "contact"):
            continue
        link_map.append((txt, href))

    def resolve_url(href: str) -> str:
        if href.startswith("http://") or href.startswith("https://"):
            return href
        if href.startswith("/"):
            m = re.match(r"^(https?://[^/]+)", site_url)
            if m:
                return m.group(1) + href
        return href

    def best_url_for_name(name: str) -> str:
        n = norm_space(name)
        n_low = n.lower()
        best = ""
        best_score = 0
        for txt, href in link_map:
            t = txt.strip()
            t_low = t.lower()
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
        if SKU_RE.search(s) or PRICE_RE.search(s) or DATE_RE.search(s):
            return False
        bad_starts = ("release date", "retail price", "style code", "sku", "updated", "update")
        if any(s.lower().startswith(b) for b in bad_starts):
            return False
        if len(s) <= 10 and s.isupper():
            return False
        return True

    shoes: List[dict] = []
    seen_ids = set()

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

        if not release_date and not price and not sku:
            continue

        fields = {
            "name": norm_space(name),
            "release_date": release_date,
            "price": price,
            "sku": sku,
            "url": url,
            "source_page": site_url,
        }

        if detail_mode:
            snippet = [x for x in snippet_lines if not looks_noisy(x)]
            fields["detail_snippet"] = "\n".join(snippet[:20])

        sid = stable_shoe_id(fields["name"], fields["url"])
        if sid in seen_ids:
            continue
        seen_ids.add(sid)

        shoes.append({"id": sid, "fields": fields})

    return shoes


def build_item_line(fields: dict) -> str:
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
def tg_send(
    bot_token: str,
    chat_id: str,
    text: str,
    *,
    parse_mode: Optional[str] = None,   # "HTML" / "MarkdownV2" / None
    disable_preview: bool = True,
) -> None:
    """
    Telegram Bot API sender:
    - Auto split long messages (Telegram limit: 4096)
    - Retry on transient failures
    """
    if not bot_token or not chat_id:
        return

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    # Telegram hard limit is 4096 chars per message.
    MAX_LEN = 3900  # 留余量
    chunks = [text[i:i + MAX_LEN] for i in range(0, len(text or ""), MAX_LEN)] or [""]

    last_err = None
    for chunk in chunks:
        payload = {
            "chat_id": chat_id,
            "text": chunk,
            "disable_web_page_preview": disable_preview,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode

        # 3 retries
        ok = False
        for i in range(3):
            try:
                r = requests.post(url, json=payload, timeout=30)
                r.raise_for_status()
                data = r.json()
                if not data.get("ok"):
                    raise RuntimeError(f"telegram api not ok: {data}")
                ok = True
                break
            except Exception as e:
                last_err = e
                time.sleep(1.5 * (i + 1))

        if not ok:
            raise RuntimeError(f"Telegram send failed after retries: {last_err}")


# -------- WeCom 应用消息推送（企业微信） --------
def wecom_get_token(corp_id: str, app_secret: str) -> str:
    url = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
    r = requests.get(url, params={"corpid": corp_id, "corpsecret": app_secret}, timeout=30)
    r.raise_for_status()
    data = r.json()
    if data.get("errcode") != 0:
        raise RuntimeError(f"wecom gettoken error: {data}")
    return data["access_token"]


def wecom_send_text(access_token: str, agent_id: str, to_user: str, content: str) -> None:
    url = "https://qyapi.weixin.qq.com/cgi-bin/message/send"
    payload = {
        "touser": to_user,  # 多个用 | 分隔
        "msgtype": "text",
        "agentid": int(agent_id),
        "text": {"content": content},
        "safe": 0,
    }
    r = requests.post(url, params={"access_token": access_token}, json=payload, timeout=30)
    r.raise_for_status()
    data = r.json()
    if data.get("errcode") != 0:
        raise RuntimeError(f"wecom send error: {data}")


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
    parts: List[str] = []
    parts.append(f"🔥 Sneaker Update (V3) | {site_name}")
    parts.append(f"Mode: {'B' if detail_mode else 'A'} | {now_taipei()}")
    parts.append("")
    count = 0

    def add_item(prefix: str, fields: dict):
        nonlocal count
        if count >= 12:
            return
        parts.append(
            f"{prefix} {fields.get('name','Unknown')} | "
            f"{fields.get('release_date') or 'TBD'} | "
            f"{fields.get('price') or 'TBD'}"
        )
        parts.append(fields.get("url") or fields.get("source_page") or "")
        parts.append("")
        count += 1

    for it in created:
        add_item("✅ NEW:", it["fields"])
    for _old_it, new_it, _changes in updated:
        add_item("🔁 UPD:", new_it["fields"])

    if count == 0:
        parts.append("No actionable changes.")

    return "\n".join(parts).strip()


def build_wecom_text(
    site_name: str,
    created: List[dict],
    updated: List[Tuple[dict, dict, List[str]]],
    detail_mode: bool,
) -> str:
    """
    企业微信消息建议短一些（text 最多 2048 字符左右更稳妥）。
    这里发“精简版”，避免过长被拒绝。
    """
    parts: List[str] = []
    parts.append(f"👟 Sneaker Update | {site_name}")
    parts.append(f"Mode: {'B' if detail_mode else 'A'} | {now_taipei()}")
    parts.append("")

    count = 0

    def add(fields: dict, prefix: str):
        nonlocal count
        if count >= 8:
            return
        name = fields.get("name", "Unknown")
        date = fields.get("release_date") or "TBD"
        price = fields.get("price") or "TBD"
        url = fields.get("url") or fields.get("source_page") or ""
        parts.append(f"{prefix} {name}")
        parts.append(f"  - Date: {date} | Price: {price}")
        if url:
            parts.append(f"  - {url}")
        parts.append("")
        count += 1

    for it in created:
        add(it["fields"], "✅ NEW")
    for _old, new_it, _chg in updated:
        add(new_it["fields"], "🔁 UPD")

    if count == 0:
        parts.append("No actionable changes.")

    return "\n".join(parts).strip()


# ===== notified 结构：notified[date][site_name] = [event_id...] =====
def ensure_notified_bucket(state: dict, day: str, site_name: str) -> List[str]:
    # 确保 notified 是 dict
    if "notified" not in state or not isinstance(state["notified"], dict):
        state["notified"] = {}

    # 兼容旧结构：notified[day] 可能是 list（你之前那版脚本就是这样）
    if day in state["notified"] and isinstance(state["notified"][day], list):
        legacy = state["notified"][day]
        state["notified"][day] = {"__legacy__": legacy}

    # 确保 notified[day] 是 dict
    if day not in state["notified"] or not isinstance(state["notified"][day], dict):
        state["notified"][day] = {}

    # 确保 notified[day][site_name] 是 list
    if site_name not in state["notified"][day] or not isinstance(state["notified"][day][site_name], list):
        state["notified"][day][site_name] = []

    return state["notified"][day][site_name]


def main():
    token = os.getenv("GITHUB_TOKEN", "").strip()
    repo = os.getenv("GITHUB_REPOSITORY", "").strip()
    issue_number = os.getenv("ISSUE_NUMBER", "").strip()

    detail_mode = os.getenv("DETAIL_MODE", "").strip() == "1"  # 0=A(默认), 1=B(更敏感)

    tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    tg_chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    # 企业微信（WeCom）应用消息参数
    wecom_corp_id = os.getenv("WECOM_CORP_ID", "").strip()
    wecom_agent_id = os.getenv("WECOM_AGENT_ID", "").strip()
    wecom_secret = os.getenv("WECOM_APP_SECRET", "").strip()
    wecom_to_user = os.getenv("WECOM_TO_USER", "").strip()

    # ✅ 可控测试：只有 TELEGRAM_TEST=1 才会发一次测试消息（避免 schedule 每次都发）
    if os.getenv("TELEGRAM_TEST", "").strip() == "1" and tg_token and tg_chat_id:
        try:
            tg_send(tg_token, tg_chat_id, f"✅ Telegram 通道已连通（GitHub Actions 测试）\n{now_taipei()}")
            print("[OK] Telegram test message sent.")
        except Exception as e:
            print(f"[WARN] Telegram test message failed: {e}")

    # ✅ 企业微信可控测试：只有 WECOM_TEST=1 才会发一次测试消息
    if os.getenv("WECOM_TEST", "").strip() == "1":
        print("[DEBUG] WECOM_CORP_ID exists:", bool(wecom_corp_id))
        print("[DEBUG] WECOM_AGENT_ID:", wecom_agent_id)
        print("[DEBUG] WECOM_TO_USER:", wecom_to_user)

        if not (wecom_corp_id and wecom_agent_id and wecom_secret and wecom_to_user):
            raise RuntimeError("Missing WeCom env vars for test.")

        token_wecom = wecom_get_token(wecom_corp_id, wecom_secret)
        wecom_send_text(
            token_wecom,
            wecom_agent_id,
            wecom_to_user,
            f"✅ 企业微信应用消息已连通（GitHub Actions 测试）\n{now_taipei()}",
        )
        print("[OK] WeCom test message sent.")

    if not SITES:
        print("[WARN] SITES 为空，请在 monitor_calendars.py 顶部填入要监控的网址。")
        return

    state = load_state()
    today = today_taipei()

    for site in SITES:
        site_name = site["name"]
        site_url = site["url"]

        # 每个站点单独一个去重桶
        notified_bucket = ensure_notified_bucket(state, today, site_name)

        print(f"\n[CHECK] {site_name} | {site_url}")
        html = fetch_html(site_url)
        soup = soup_from_html(html)

        current = extract_candidate_shoes(site_url, soup, detail_mode=detail_mode)

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
                changes = diff_fields(old_fields, cur_fields)
                updated.append((old_it, cur_it, changes))

        # 移除（仅参考）
        for sid, old_it in prev_shoes.items():
            if sid not in cur_map:
                removed.append(old_it)

        actionable = bool(created or updated)
        if not actionable:
            print("[OK] No actionable shoe-level change.")
            state.setdefault("shoes", {})
            state["shoes"][site_url] = {
                sid: {"hash": hash_fields(it["fields"]), "fields": it["fields"]}
                for sid, it in cur_map.items()
            }
            continue

        # 站点内合并通知：计算 event_ids
        event_ids: List[str] = []
        for it in created:
            event_ids.append(event_id_for(site_url, it["id"], hash_fields(it["fields"])))
        for _old_it, new_it, _changes in updated:
            event_ids.append(event_id_for(site_url, new_it["id"], hash_fields(new_it["fields"])))

        if event_ids and all(eid in notified_bucket for eid in event_ids):
            print("[SKIP] Duplicate events for this site today, already notified.")
        else:
            comment_body = build_comment_body(site_name, site_url, created, updated, removed, detail_mode=detail_mode)

            # GitHub Issue 评论（邮件来源）
            if token and repo and issue_number:
                print(f"[ALERT] Posting comment to fixed issue #{issue_number} ...")
                gh_comment_issue(repo, token, int(issue_number), comment_body)
                print("[OK] Comment added.")
            else:
                print("[WARN] Missing GITHUB_TOKEN / GITHUB_REPOSITORY / ISSUE_NUMBER, skip Issue comment.")

            # Telegram（只在有 created/updated 时才会走到这里）
            if tg_token and tg_chat_id:
                tg_text = build_tg_text(site_name, created, updated, detail_mode=detail_mode)
                try:
                    tg_send(tg_token, tg_chat_id, tg_text, parse_mode=None, disable_preview=True)
                    print("[OK] Telegram sent.")
                except Exception as e:
                    print(f"[WARN] Telegram send failed: {e}")
            else:
                print("[WARN] Missing TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID, skip Telegram.")

            # 企业微信 WeCom（只在有 created/updated 时才会走到这里）
            if wecom_corp_id and wecom_agent_id and wecom_secret and wecom_to_user:
                wecom_text = build_wecom_text(site_name, created, updated, detail_mode=detail_mode)
                try:
                    token_wecom = wecom_get_token(wecom_corp_id, wecom_secret)
                    wecom_send_text(token_wecom, wecom_agent_id, wecom_to_user, wecom_text)
                    print("[OK] WeCom sent.")
                except Exception as e:
                    print(f"[WARN] WeCom send failed: {e}")
            else:
                print("[WARN] Missing WECOM_CORP_ID / WECOM_AGENT_ID / WECOM_APP_SECRET / WECOM_TO_USER, skip WeCom.")

            # 写入已通知列表（按 date + site_name）
            for eid in event_ids:
                if eid not in notified_bucket:
                    notified_bucket.append(eid)

        # 更新鞋库 state
        state.setdefault("shoes", {})
        state["shoes"][site_url] = {
            sid: {"hash": hash_fields(it["fields"]), "fields": it["fields"]}
            for sid, it in cur_map.items()
        }

    save_state(state)
    print("\n[DONE] State saved.")


if __name__ == "__main__":
    main()
