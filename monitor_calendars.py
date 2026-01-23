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

# 你原本的站点列表可以继续用；如果你之前还有 selector/解析逻辑，也可以后续再加回去
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

# 识别“像鞋名”的行：尽量保守，避免把字段/链接当鞋名
def is_probable_shoe_name(line: str) -> bool:
    s = line.strip()
    if len(s) < 8:
        return False
    if s.lower().startswith("http"):
        return False
    # 明显字段行不要
    for pat in FIELD_PATTERNS.values():
        if pat.search(s):
            return False
    # 太像纯日期/纯价格的不要
    if re.fullmatch(r"[\$¥€]?\s*\d+(\.\d+)?", s):
        return False
    if re.fullmatch(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", s):
        return False
    # 常见垃圾后缀
    if any(k in s.lower() for k in NOISE_KEYWORDS):
        return False
    return True

def brand_of(shoe_name: str) -> str:
    s = shoe_name.lower()
    # Jordan 优先
    if "air jordan" in s or s.startswith("jordan") or "jordan brand" in s:
        return "Jordan"
    # Nike & 子系列
    if s.startswith("nike") or "kobe" in s or "lebron" in s or "air max" in s or "dunk" in s or "air force" in s:
        return "Nike"
    # New Balance
    if s.startswith("new balance") or "nb " in s or "990" in s or "991" in s or "992" in s or "993" in s:
        return "New Balance"
    # adidas
    if s.startswith("adidas") or "yeezy" in s:
        return "adidas"
    # ASICS
    if s.startswith("asics") or "gel-" in s:
        return "ASICS"
    # Puma
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
    return {"sites": {}, "notified": {}}  # notified[date][source] = set(event_id)

def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

def fetch_html(url: str) -> str:
    r = requests.get(url, timeout=30, headers={"User-Agent": "ReleaseMonitor/2.0"})
    r.raise_for_status()
    return r.text

def html_to_lines(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    # 去掉脚本样式
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
        # 过长的 UI 文案也过滤掉
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
    """
    解析 unified diff，并尽可能把字段变化归因到“最近出现的鞋名”上。
    输出结构：
    {
      brand: {
        "new_shoes": [shoe],
        "removed_shoes": [shoe],
        "release_date_changes": [(shoe, old, new)],
        "price_changes": [(shoe, old, new)],
        "other_notes": [...]
      }
    }
    """
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

    # 先把纯新增/移除鞋名放入桶（品牌分类）
    for shoe in added_shoes:
        b = brand_of(shoe)
        ensure_brand(b)
        buckets[b]["new_shoes"].append(shoe)

    for shoe in removed_shoes:
        b = brand_of(shoe)
        ensure_brand(b)
        buckets[b]["removed_shoes"].append(shoe)

    # 再尝试解析“字段变动”，并归因到最近鞋名
    last_shoe = None
    removed_fields = {}  # shoe -> {field: value}
    added_fields = {}    # shoe -> {field: value}

    for raw in diff_text.splitlines():
        if raw.startswith(("---", "+++", "@@")):
            continue
        if not raw:
            continue

        prefix = raw[0]
        content = raw[1:].strip() if prefix in (" ", "+", "-") else raw.strip()

        # 更新 last_shoe：上下文行 or 新增行里遇到像鞋名的内容
        if is_probable_shoe_name(content):
            last_shoe = content

        # 只关心 + / - 行的字段
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

    # 生成“重要变动”列表（发售日 / 价格）
    shoes_union = set(removed_fields.keys()) | set(added_fields.keys())
    for shoe in shoes_union:
        b = brand_of(shoe)
        ensure_brand(b)
        old_f = removed_fields.get(shoe, {})
        new_f = added_fields.get(shoe, {})

        # 发售日变动
        if "release_date" in old_f and "release_date" in new_f and old_f["release_date"] != new_f["release_date"]:
            buckets[b]["release_date_changes"].append((shoe, old_f["release_date"], new_f["release_date"]))
        # 价格变动
        if "price" in old_f and "price" in new_f and old_f["price"] != new_f["price"]:
            buckets[b]["price_changes"].append((shoe, old_f["price"], new_f["price"]))

    return buckets

def build_issue_title(source_name: str) -> str:
    return f"🔥 Sneaker Release Update | {source_name} | {today_taipei()}"

def build_issue_body(source_name: str, url: str, buckets: dict, diff_text: str) -> str:
    lines = []
    lines.append(f"🔔 **Sneaker Release Update Detected**")
    lines.append(f"- **来源**：{source_name}")
    lines.append(f"- **原文链接**：{url}")
    lines.append(f"- **检测时间（台北）**：{now_taipei()}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 品牌排序：你关心的品牌优先
    brand_order = ["Nike", "Jordan", "New Balance", "adidas", "ASICS", "Puma", "Other"]
    brands = [b for b in brand_order if b in buckets] + [b for b in buckets.keys() if b not in brand_order]

    # 只推送重要变化：新鞋 / 发售日 / 价格（移除鞋款可选保留）
    for brand in brands:
        block = buckets[brand]
        important_exists = bool(block["new_shoes"] or block["release_date_changes"] or block["price_changes"])
        if not important_exists:
            continue

        lines.append(f"## 🧩 {brand}")
        # ✅ 新增鞋款
        if block["new_shoes"]:
            lines.append("### ✅ 新增鞋款")
            for shoe in block["new_shoes"][:20]:
                lines.append(f"- {shoe}")
            if len(block["new_shoes"]) > 20:
                lines.append(f"- …以及另外 {len(block['new_shoes']) - 20} 双（见原始 diff）")

        # 📅 发售日期变动
        if block["release_date_changes"]:
            lines.append("")
            lines.append("### 📅 发售日期变动")
            for shoe, oldv, newv in block["release_date_changes"][:30]:
                lines.append(f"- {shoe}  ·  **{oldv} → {newv}**")

        # 💰 价格变动
        if block["price_changes"]:
            lines.append("")
            lines.append("### 💰 价格变动")
            for shoe, oldv, newv in block["price_changes"][:30]:
                lines.append(f"- {shoe}  ·  **{oldv} → {newv}**")

        lines.append("")
        lines.append("---")
        lines.append("")

    # 如果全都被过滤了，也给一个提示（避免“变了但没看到”的误会）
    any_visible = any(
        (buckets[b]["new_shoes"] or buckets[b]["release_date_changes"] or buckets[b]["price_changes"])
        for b in buckets
    )
    if not any_visible:
        lines.append("⚠️ 本次检测到页面变化，但不属于 2.0 规则定义的“重要变化”（新鞋 / 发售日 / 价格）。")
        lines.append("你可以在下方原始 diff 查看全部变更证据。")
        lines.append("")
        lines.append("---")
        lines.append("")

    # 原始 diff 折叠（证据）
    lines.append("<details>")
    lines.append("<summary>📎 原始 diff（证据，点击展开）</summary>")
    lines.append("")
    lines.append("```diff")
    lines.append(diff_text.strip())
    lines.append("```")
    lines.append("")
    lines.append("</details>")

    return "\n".join(lines)

# —— GitHub API：创建 Issue + 同日合并（用 comment 追加）——
def gh_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ReleaseMonitor/2.0",
    }

def gh_search_issue_number(repo: str, token: str, title: str) -> int | None:
    # 用 Search API 按 title 找同日同来源 Issue（open）
    q = f'repo:{repo} is:issue state:open in:title "{title}"'
    url = f"https://api.github.com/search/issues?q={requests.utils.quote(q)}"
    r = requests.get(url, headers=gh_headers(token), timeout=30)
    r.raise_for_status()
    data = r.json()
    items = data.get("items", [])
    if not items:
        return None
    return items[0].get("number")

def gh_create_issue(repo: str, token: str, title: str, body: str) -> int:
    url = f"https://api.github.com/repos/{repo}/issues"
    r = requests.post(url, headers=gh_headers(token), json={"title": title, "body": body}, timeout=30)
    r.raise_for_status()
    return r.json()["number"]

def gh_comment_issue(repo: str, token: str, issue_number: int, body: str) -> None:
    url = f"https://api.github.com/repos/{repo}/issues/{issue_number}/comments"
    r = requests.post(url, headers=gh_headers(token), json={"body": body}, timeout=30)
    r.raise_for_status()

def event_id_for(site_url: str, new_hash: str) -> str:
    # “同一次变化”去重用：site_url + new_hash
    return hashlib.sha256(f"{site_url}::{new_hash}".encode("utf-8")).hexdigest()

def main():
    token = os.getenv("GITHUB_TOKEN", "").strip()
    repo = os.getenv("GITHUB_REPOSITORY", "").strip()

    if not SITES:
        print("[WARN] SITES 为空，请在 monitor_calendars.py 顶部填入要监控的网址。")
        return

    state = load_state()
    today = today_taipei()

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
            # 仍然更新 hash/lines（避免一直重复触发），但不通知
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

        title = build_issue_title(name)
        body = build_issue_body(name, url, buckets, diff_text)

        if not token or not repo:
            print("[WARN] 未找到 GITHUB_TOKEN 或 GITHUB_REPOSITORY, 跳过创建 Issue / Comment")
        else:
            issue_no = gh_search_issue_number(repo, token, title)
            if issue_no is None:
                print("[CREATE] Creating daily issue...")
                issue_no = gh_create_issue(repo, token, title, body)
                print(f"[OK] Issue created: #{issue_no}")
            else:
                # 同日同来源合并：追加 comment（仍然会触发邮件通知）
                print(f"[MERGE] Found existing daily issue #{issue_no}, adding comment...")
                comment = f"### 🔁 Update @ {now_taipei()}\n\n" + body
                gh_comment_issue(repo, token, issue_no, comment)
                print("[OK] Comment added.")

        # 更新 state
        state["notified"][today][name].append(eid)
        state["sites"][url] = {"hash": new_hash, "lines": new_lines}

    save_state(state)
    print("\n[DONE] State saved.")

if __name__ == "__main__":
    main()
