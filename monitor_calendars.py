import json
import time
import hashlib
import difflib
import os
from pathlib import Path

import requests
from bs4 import BeautifulSoup

STATE_PATH = Path("state.json")

SITES = [
    {
        "name": "A | SneakerNews | Air Jordan Release Dates",
        "url": "https://sneakernews.com/air-jordan-release-dates/",
    },
    {
        "name": "B | SneakerNews | Release Dates Calendar",
        "url": "https://sneakernews.com/release-dates/",
    },
    {
        "name": "C | SneakerBarDetroit | Sneaker Release Dates",
        "url": "https://sneakerbardetroit.com/sneaker-release-dates/",
    },
    {
        "name": "D | SneakerBarDetroit | Air Jordan Release Dates",
        "url": "https://sneakerbardetroit.com/air-jordan-release-dates/",
    },
]

REQUEST_TIMEOUT = 25
POLITE_SLEEP_SECONDS = 2
MAX_DIFF_LINES = 40


def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {}


def save_state(state):
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_text(raw):
    lines = [ln.strip() for ln in raw.splitlines()]
    lines = [ln for ln in lines if ln]

    filtered = []
    for ln in lines:
        has_digit = any(ch.isdigit() for ch in ln)
        if has_digit or len(ln) >= 20:
            filtered.append(ln)

    seen = set()
    uniq = []
    for ln in filtered:
        if ln in seen:
            continue
        seen.add(ln)
        uniq.append(ln)

    return "\n".join(uniq)


def fetch_page_text(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; ReleaseCalendarMonitor/1.0)",
        "Accept-Language": "en-US,en;q=0.9",
    }
    r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = soup.get_text(separator="\n")
    return normalize_text(text)


def diff_summary(old, new):
    old_lines = (old or "").splitlines()
    new_lines = (new or "").splitlines()

    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile="before",
        tofile="after",
        lineterm="",
        n=1,
    )

    changes = []
    for ln in diff:
        if ln.startswith(("@@", "---", "+++", "before", "after")):
            continue
        if ln.startswith("+") or ln.startswith("-"):
            if ln.startswith("+++ ") or ln.startswith("--- "):
                continue
            changes.append(ln)

    if not changes:
        return "（检测到变化，但未能提取到可读差异：可能是页面结构或广告位变动。）"

    if len(changes) > MAX_DIFF_LINES:
        changes = changes[:MAX_DIFF_LINES] + ["...（差异过多已截断）"]

    return "\n".join(changes)


def create_github_issue(title, body):
    token = os.getenv("GITHUB_TOKEN")
    repo = os.getenv("GITHUB_REPOSITORY")

    if not token or not repo:
        print("[WARN] 未找到 GITHUB_TOKEN 或 GITHUB_REPOSITORY，跳过创建 Issue")
        return

    api_url = f"https://api.github.com/repos/{repo}/issues"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }

    payload = {"title": title, "body": body}

    r = requests.post(api_url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
    if r.status_code >= 300:
        print("[ERROR] 创建 Issue 失败：", r.status_code, r.text)
    else:
        print("[OK] 已创建 Issue（将触发 GitHub 邮件通知）")


def main():
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
            state[name] = {"url": url, "hash": new_hash, "text": new_text}
            print(f"[INIT] {name} 已建立基线（首次运行不判定为更新）")
        elif old_hash != new_hash:
            summary = diff_summary(old_text, new_text)
            updates_found.append((name, url, summary))
            state[name] = {"url": url, "hash": new_hash, "text": new_text}
            print(f"[UPDATE] {name} 检测到内容变化")
        else:
            print(f"[OK] {name} 无变化")

        time.sleep(POLITE_SLEEP_SECONDS)

    save_state(state)

    if updates_found:
        print("\n==================== 变化摘要 ====================")

        blocks = []
        for name, url, summary in updates_found:
            blocks.append(f"### {name}\n{url}\n```diff\n{summary}\n```")

            print(f"\n[{name}]")
            print(url)
            print(summary)

        run_id = os.getenv("GITHUB_RUN_ID", "")
        title = "📅 Release Calendar 更新检测到变化"
        if run_id:
            title += f" (run {run_id})"

        create_github_issue(
            title=title,
            body="\n\n".join(blocks),
        )

        print("\n提示：你收到邮件后，把变更页面链接发给我，我就能按 Cold Treasure 模板写成稿。")
    else:
        print("\n无更新。")


if __name__ == "__main__":
    main()
