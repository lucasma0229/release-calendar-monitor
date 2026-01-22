# release-calendar-monitor

import json
import time
import hashlib
import difflib
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

STATE_PATH = Path("state.json")

SITES = [
    # A: SneakerNews Air Jordan release dates (contains 2026 section)
    {
        "name": "A | SneakerNews | Air Jordan Release Dates",
        "url": "https://sneakernews.com/air-jordan-release-dates/",
    },
    # B: SneakerNews general release dates calendar (often updated)
    {
        "name": "B | SneakerNews | Release Dates Calendar",
        "url": "https://sneakernews.com/release-dates/",
    },
    # C: SBD sneaker release dates (2026-oriented page)
    {
        "name": "C | SneakerBarDetroit | Sneaker Release Dates",
        "url": "https://sneakerbardetroit.com/sneaker-release-dates/",
    },
    # D: SBD Air Jordan release dates
    {
        "name": "D | SneakerBarDetroit | Air Jordan Release Dates",
        "url": "https://sneakerbardetroit.com/air-jordan-release-dates/",
    },
]

# 建议频率：30-60 分钟（别太高）
REQUEST_TIMEOUT = 25
POLITE_SLEEP_SECONDS = 2

# 输出变更摘要时最多显示多少行差异
MAX_DIFF_LINES = 40


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_text(raw: str) -> str:
    """
    把页面文本“压平”成可比对的稳定文本：
    - 统一空白
    - 去掉过多空行
    """
    lines = [ln.strip() for ln in raw.splitlines()]
    lines = [ln for ln in lines if ln]  # 去空行
    # 避免“日期/价格/标题”被淹没：保留较长行与含数字行
    filtered = []
    for ln in lines:
        has_digit = any(ch.isdigit() for ch in ln)
        if has_digit or len(ln) >= 20:
            filtered.append(ln)
    # 再做一次去重（保持顺序）
    seen = set()
    uniq = []
    for ln in filtered:
        if ln in seen:
            continue
        seen.add(ln)
        uniq.append(ln)
    return "\n".join(uniq)


def fetch_page_text(url: str) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; ReleaseCalendarMonitor/1.0)",
        "Accept-Language": "en-US,en;q=0.9",
    }
    r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")

    # 去掉脚本/样式，减少噪音
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = soup.get_text(separator="\n")
    return normalize_text(text)


def diff_summary(old: str, new: str) -> str:
    """
    给出一个可读的差异摘要（类似 git diff 的行级变化）。
    """
    old_lines = old.splitlines()
    new_lines = new.splitlines()

    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile="before",
        tofile="after",
        lineterm="",
        n=1,
    )

    # 只取最关键的变化行：+ / - 开头
    changes = []
    for ln in diff:
        if ln.startswith(("@@", "---", "+++", "before", "after")):
            continue
        if ln.startswith("+") or ln.startswith("-"):
            # 排除 diff 自带的 +++ / ---（上面已过滤），这里保底
            if ln.startswith("+++ ") or ln.startswith("--- "):
                continue
            changes.append(ln)

    if not changes:
        return "（检测到变化，但未能提取到可读差异。可能是页面结构小改动/广告位变动。）"

    # 截断，避免刷屏
    if len(changes) > MAX_DIFF_LINES:
        changes = changes[:MAX_DIFF_LINES] + ["...（差异过多已截断）"]

    return "\n".join(changes)


def main() -> None:
    state = load_state()
    updates_found = []

    for site in SITES:
        name = site["name"]
        url = site["url"]

        try:
            new_text = fetch_page_text(url)
        except Exception as e:
            print(f"[ERROR] {name} 抓取失败：{e}")
            time.sleep(POLITE_SLEEP_SECONDS)
            continue

        new_hash = sha256_text(new_text)

        prev = state.get(name, {})
        old_hash = prev.get("hash")
        old_text = prev.get("text")

        if old_hash is None:
            # 第一次记录
            state[name] = {"url": url, "hash": new_hash, "text": new_text}
            print(f"[INIT] {name} 已建立基线（首次运行不判定为更新）")
        elif old_hash != new_hash:
            summary = diff_summary(old_text or "", new_text)
            updates_found.append((name, url, summary))
            # 更新基线
            state[name] = {"url": url, "hash": new_hash, "text": new_text}
            print(f"[UPDATE] {name} 检测到内容变化")
        else:
            print(f"[OK] {name} 无变化")

        time.sleep(POLITE_SLEEP_SECONDS)

    save_state(state)

    if updates_found:
        print("\n==================== 变化摘要 ====================")
        for name, url, summary in updates_found:
            print(f"\n[{name}]")
            print(url)
            print(summary)
        print("\n提示：把“发生变化的页面链接”发给我，我就能按 Cold Treasure 模板把更新内容写成日报/单篇稿。")
    else:
        print("\n无更新。")


if __name__ == "__main__":
    main()
