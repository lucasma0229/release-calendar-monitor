import os
import re
import json
import hashlib
import difflib
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

STATE_PATH = Path("state.json")
TZ_TAIPEI = timezone(timedelta(hours=8))

# 站点列表：先保持你现在的（后续做“鞋款级监控”会再升级）
SITES = [
    {"name": "SBD | Air Jordan Release Dates 2026", "url": "https://sneakerbardetroit.com/air-jordan-release-dates/"},
    {"name": "SBD | Sneaker Release Dates 2026", "url": "https://sneakerbardetroit.com/sneaker-release-dates/"},
    {"name": "SneakerNews | Jordan Release Date Calendar 2026", "url": "https://sneakernews.com/air-jordan-release-dates/"},
    {"name": "SneakerNews | Sneaker Release Dates Calendar 2025", "url": "https://sneakernews.com/release-dates/"},
]

# —— 噪音过滤：你可以不断加关键词来“降噪” ——
NOISE_KEYWORDS = [
    "advertisement", "sponsored", "cookie", "privacy", "subscribe",
    "twitter", "instagram", "facebook", "tiktok", "youtube", "pinterest",
    "sign up", "log in", "login", "newsletter", "share", "follow",
    "buy now", "shop now", "where to buy", "stockx", "goat", "ebay",
    "评论", "隐私", "条款", "订阅", "关注", "分享", "登录", "注册", "广告",
]

# —— 只关心的重要字段（2.0 版）——
FIELD_PATTERNS = {
    "release_date": re.compile(r"(release\s*date|发售日|发布日期|发售日期)\s*[:：]?\s*(.+)", re.I),
    "price": re.compile(r"(retail\s*price|price|定价|售价)\s*[:：]?\s*(.+)", re.I),
    "sku": re.compile(r"(style\s*code|sku|货号)\s*[:：]?\s*(.+)", re.I),
}

def is_probable_shoe_name(line: str) -> bool:
    s = line.strip()
    if len(s) < 8:
        return False
    if s.lower().startswith("http"):
        return False
    for pat in FIELD_PATTERNS.values():
        if pat.search(s):
            return False
    if re.fullmatch(r"[\$¥€]?\s*\d+(\.\d+)?", s):
        return False
    if re.fullmatch(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", s):
        return False
    if any(k in s.lower() for k in NOISE_KEYWORDS):
        return False
    return True

def brand_of(shoe_name: str) -> str:
    s = shoe_name.lower()
    if "air jordan" in s or s.startswith("jordan") or "jordan brand" in s:
        return "Jordan"
    if s.startswith("nike") or "kobe" in s or "lebron" in s or "air max" in s or "dunk" in s or "air force" in s:
        return "Nike"
    if s.startswith("new balance") or "nb " in s or "990" in s or "991" in s or "992" in s or "993" in s:
        return "New Balance"
    if s.startswith("adidas") or "yeezy" in s:
        return "adidas"
    if s.startswith("asics") or "gel-" in s:
        return "ASICS"
    if s.startswith("puma"):
        return "Puma"
    return "Other"

def now_taipei() -> str:
    return datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M")

def today_taipei() -> str:
    return datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d")

def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"sites": {}, "notified": {}}  # notified[date][source] = list(event_id)

def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

def fetch_html(url: str) -> str:
    r = requests.get(url, timeout=30, headers={"User-Agent": "ReleaseMonitor/2.0"})
    r.raise_for_status()
    return r.text

def html_to_lines(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text("\n")

    lines = []
    for raw in text.splitlines():
        s = " ".join(raw.strip().split())
        if not s:
            continue
        low = s.lower()
        if any(k in low for k in NOISE_KEYWORDS):
            continue
        if len(s) > 180:
            continue
        lines.append(s)
    return lines

def fingerprint(lines: list[str]) -> str:
    joined = "\n".join(lines).encode("utf-8", errors="ignore")
    return hashlib.sha256(joined).hexdigest()

def unified_diff(old_lines: list[str], new_lines: list[str]) -> str:
    diff = difflib.unified_diff(
        old_lines, new_lines,
        fromfile="before", tofile="after",
        lineterm="",
        n=2
    )
    return "\n".join(diff)

def parse_diff_to_events(diff_text: str, old_lines: list[str], new_lines: list[str]) -> dict:
    old_shoes = {l for l in old_lines if is_probable_shoe_name(l)}
    new_shoes = {l for l in new_lines if is_probable_shoe_name(l)}

    added_shoes = sorted(list(new_shoes - old_shoes))
    removed_shoes = sorted(list(old_shoes - new_shoes))

    buckets = {}

    def ensure_brand(b: str):
        if b not in buckets:
            buckets[b] = {
                "new_shoes": [],
                "removed_shoes": [],
                "release_date_changes": [],
                "price_changes": [],
                "other_notes": [],
            }

    for shoe in added_shoes:
        b = brand_of(shoe)
        ensure_brand(b)
        buckets[b]["new_shoes"].append(shoe)

    for shoe in removed_shoes:
        b = brand_of(shoe)
        ensure_brand(b)
        buckets[b]["removed_shoes"].append(shoe)

    last_shoe = None
    removed_fields = {}
    added_fields = {}

    for raw in diff_text.splitlines():
        if raw.startswith(("---", "+++", "@@")):
            continue
        if not raw:
            continue

        prefix = raw[0]
        content = raw[1:].strip() if prefix in (" ", "+", "-") else raw.strip()

        if is_probable_shoe_name(content):
            last_shoe = content

        if prefix not in ("+", "-"):
            continue
        if not last_shoe:
            continue

        for field, pat in FIELD_PATTERNS.items():
            m = pat.search(content)
            if not m:
                continue
            value = m.group(2).strip()
            if prefix == "-":
                removed_fields.setdefault(last_shoe, {})[field] = value
            else:
                added_fields.setdefault(last_shoe, {})[field] = value

    shoes_union = set(removed_fields.keys()) | set(added_fields.keys())
    for shoe in shoes_union:
        b = brand_of(shoe)
        ensure_brand(b)
        old_f = removed_fields.get(shoe, {})
        new_f = added_fields.get(shoe, {})

        if "release_date" in old_f and "release_date" in new_f and old_f["release_date"] != new_f["release_date"]:
            buckets[b]["release_date_changes"].append((shoe, old_f["release_date"], new_f["release_date"]))

        if "price" in old_f and "price" in new_f and old_f["price"] != new_f["price"]:
            buckets[b]["price_changes"].append((shoe, old_f["price"], new_f["price"]))

    return buckets

def build_fixed_issue_comment(source_name: str, url: str, buckets: dict, diff_text: str) -> str:
    """
    固定 Issue 评论内容：
    - 不再创建 daily issue
    - 只要有“重要变化”，就往固定 Issue 追加 comment
    """
    lines = []
    lines.append(f"## 🔔 Update Detected @ {now_taipei()}")
    lines.append(f"- **来源**：{source_name}")
    lines.append(f"- **链接**：{url}")
    lines.append("")

    brand_order = ["Nike", "Jordan", "New Balance", "adidas", "ASICS", "Puma", "Other"]
    brands = [b for b in brand_order if b in buckets] + [b for b in buckets.keys() if b not in brand_order]

    for brand in brands:
        block = buckets[brand]
        important_exists = bool(block["new_shoes"] or block["release_date_changes"] or block["price_changes"])
        if not important_exists:
            continue

        lines.append(f"### 🧩 {brand}")

        if block["new_shoes"]:
            lines.append("**✅ 新增鞋款**")
            for shoe in block["new_shoes"][:20]:
                lines.append(f"- {shoe}")

        if block["release_date_changes"]:
            lines.append("")
            lines.append("**📅 发售日期变动**")
            for shoe, oldv, newv in block["release_date_changes"][:30]:
                lines.append(f"- {shoe} · **{oldv} → {newv}**")

        if block["price_changes"]:
            lines.append("")
            lines.append("**💰 价格变动**")
            for shoe, oldv, newv in block["price_changes"][:30]:
                lines.append(f"- {shoe} · **{oldv} → {newv}**")

        lines.append("")

    # 证据：diff 折叠
    lines.append("<details>")
    lines.append("<summary>📎 原始 diff（证据，点击展开）</summary>")
    lines.append("")
    lines.append("```diff")
    lines.append(diff_text.strip())
    lines.append("```")
    lines.append("")
    lines.append("</details>")

    return "\n".join(lines)

# —— GitHub API：只做“追加评论到固定 Issue” —— #
def gh_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ReleaseMonitor/2.0",
    }

def gh_comment_issue(repo: str, token: str, issue_number: int, body: str) -> None:
    url = f"https://api.github.com/repos/{repo}/issues/{issue_number}/comments"
    r = requests.post(url, headers=gh_headers(token), json={"body": body}, timeout=30)
    r.raise_for_status()

def event_id_for(site_url: str, new_hash: str) -> str:
    return hashlib.sha256(f"{site_url}::{new_hash}".encode("utf-8")).hexdigest()

def main():
    token = os.getenv("GITHUB_TOKEN", "").strip()
    repo = os.getenv("GITHUB_REPOSITORY", "").strip()
    issue_number = os.getenv("ISSUE_NUMBER", "").strip()
    force_test = os.getenv("FORCE_TEST_COMMENT", "").strip() == "1"

    if not SITES:
        print("[WARN] SITES 为空，请在 monitor_calendars.py 顶部填入要监控的网址。")
        return

    state = load_state()
    today = today_taipei()

    # ✅ 强制测试模式（只执行一次就退出）
    if force_test:
        if not token or not repo or not issue_number:
            print("[WARN] FORCE_TEST_COMMENT=1 but missing env, skip.")
        else:
            test_body = f"✅ Test comment from GitHub Actions @ {now_taipei()}\n\nThis is a connectivity test."
            print(f"[ALERT] Posting TEST comment to fixed issue #{issue_number} ...")
            gh_comment_issue(repo, token, int(issue_number), test_body)
            print("[OK] Test comment added to fixed issue.")
        return
            
    for site in SITES:
        name = site["name"]
        url = site["url"]

        print(f"\n[CHECK] {name} | {url}")

        html = fetch_html(url)
        new_lines = html_to_lines(html)
        new_hash = fingerprint(new_lines)

        prev = state["sites"].get(url, {})
        old_hash = prev.get("hash")
        old_lines = prev.get("lines", [])

        if old_hash == new_hash:
            print("[OK] No important change (hash unchanged).")
            continue

        diff_text = unified_diff(old_lines, new_lines)
        buckets = parse_diff_to_events(diff_text, old_lines, new_lines)

        # 是否存在“重要变化”
        has_important = any(
            (buckets[b]["new_shoes"] or buckets[b]["release_date_changes"] or buckets[b]["price_changes"])
            for b in buckets
        )
        if not has_important:
            print("[INFO] Changed but not important (filtered). Update state without notifying.")
            state["sites"][url] = {"hash": new_hash, "lines": new_lines}
            continue

        # 去重：同一个 new_hash 不重复通知
        eid = event_id_for(url, new_hash)
        state["notified"].setdefault(today, {}).setdefault(name, [])
        if eid in state["notified"][today][name]:
            print("[SKIP] Duplicate event_id, already notified today.")
            state["sites"][url] = {"hash": new_hash, "lines": new_lines}
            continue

        comment_body = build_fixed_issue_comment(name, url, buckets, diff_text)

        if not token or not repo or not issue_number:
            print("[WARN] 缺少 GITHUB_TOKEN / GITHUB_REPOSITORY / ISSUE_NUMBER，跳过 Issue 评论通知")
        else:
            print(f"[ALERT] Posting comment to fixed issue #{issue_number} ...")
            gh_comment_issue(repo, token, int(issue_number), comment_body)
            print("[OK] Comment added to fixed issue.")

        # 更新 state
        state["notified"][today][name].append(eid)
        state["sites"][url] = {"hash": new_hash, "lines": new_lines}

    save_state(state)
    print("\n[DONE] State saved.")
    
if __name__ == "__main__":
    main()
