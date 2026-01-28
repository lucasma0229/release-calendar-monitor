import os
import re
import json
import time
import hashlib
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


# =========================
# ColdTreasure V2.0 Settings
# =========================

STATE_PATH = Path("state.json")
TZ_TAIPEI = timezone(timedelta(hours=8))
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)

REQUEST_TIMEOUT = 25
MAX_ARTICLES_PER_SOURCE = 12
MAX_PUSH_ITEMS_PER_RUN = 12  # safety cap: avoid spamming in one run

# -------------------------
# Sources to monitor
# -------------------------
CALENDAR_SITES = [
    {"name": "SBD | Sneaker Release Dates", "url": "https://sneakerbardetroit.com/sneaker-release-dates/"},
    {"name": "SBD | Air Jordan Release Dates", "url": "https://sneakerbardetroit.com/air-jordan-release-dates/"},
    {"name": "SneakerNews | Release Dates", "url": "https://sneakernews.com/release-dates/"},
    {"name": "SneakerNews | Air Jordan Release Dates", "url": "https://sneakernews.com/air-jordan-release-dates/"},
]

NEWS_SOURCES = [
    {"name": "SBD | Latest", "url": "https://sneakerbardetroit.com/"},
    {"name": "SneakerNews | Latest", "url": "https://sneakernews.com/"},
]

# =========================
# Brand / Model Intelligence
# =========================

CAT_SPORTS = "sports"
CAT_STREET = "streetwear"
CAT_LUXURY = "luxury"
CAT_JEWELRY = "jewelry"
CAT_IP = "ip"
CAT_CULTURE = "culture"

BRAND_DICT: Dict[str, List[str]] = {
    "Nike": ["Nike", "NIKE"],
    "Jordan": ["Jordan", "Air Jordan", "AJ", "Jumpman"],
    "adidas": ["adidas", "Adidas", "ADIDAS"],
    "New Balance": ["New Balance", "NB", "NewBalance"],
    "ASICS": ["ASICS", "Asics"],
    "Puma": ["Puma", "PUMA"],
    "Converse": ["Converse", "CONS"],
    "Reebok": ["Reebok"],
    "Vans": ["Vans"],
    "Under Armour": ["Under Armour", "UA"],
    "Mizuno": ["Mizuno"],
    "Brooks": ["Brooks"],
    "On": ["On Running", "On"],
    "HOKA": ["HOKA", "Hoka"],
    "Salomon": ["Salomon"],
    "Saucony": ["Saucony"],
    "Merrell": ["Merrell"],
    "Altra": ["Altra"],
    "La Sportiva": ["La Sportiva"],
    "K-Swiss": ["K-Swiss", "K Swiss"],
    "Fila": ["Fila", "FILA"],
    "Kappa": ["Kappa"],
    "Diadora": ["Diadora"],
    "Li-Ning": ["Li-Ning", "Lining", "李宁", "李寧"],
    "ANTA": ["ANTA", "安踏"],
    "361°": ["361°", "361", "361 Degrees"],
    "Xtep": ["Xtep", "特步"],
    "PEAK": ["PEAK", "Peak", "匹克"],
    "Descente": ["Descente"],
    "Umbro": ["Umbro"],
    "Skechers": ["Skechers"],
    "Timberland": ["Timberland"],
    "The North Face": ["The North Face", "TNF"],
    "Arc'teryx": ["Arc'teryx", "Arcteryx"],
    "Columbia": ["Columbia"],
    "YEEZY": ["YEEZY", "Yeezy"],

    "Supreme": ["Supreme"],
    "Stüssy": ["Stussy", "Stüssy"],
    "BAPE": ["BAPE", "A Bathing Ape"],
    "CLOT": ["CLOT"],
    "KITH": ["Kith", "KITH"],
    "Off-White": ["Off-White", "Off White"],
    "Fragment": ["Fragment", "fragment design"],
    "A Ma Maniére": ["A Ma Maniére", "A Ma Maniere", "AMM"],
    "Aimé Leon Dore": ["Aimé Leon Dore", "Aime Leon Dore", "ALD"],
    "JJJJound": ["JJJJound"],

    "Dior": ["Dior"],
    "Louis Vuitton": ["Louis Vuitton", "LV"],
    "Gucci": ["Gucci"],
    "Balenciaga": ["Balenciaga"],
    "Prada": ["Prada"],
    "Tiffany & Co.": ["Tiffany", "Tiffany & Co."],
    "Swarovski": ["Swarovski"],
    "Cartier": ["Cartier"],
    "Bvlgari": ["Bvlgari", "Bulgari"],
    "Chrome Hearts": ["Chrome Hearts"],

    "Disney": ["Disney"],
    "Marvel": ["Marvel"],
    "Star Wars": ["Star Wars"],
    "NBA": ["NBA"],
    "NFL": ["NFL"],
    "PlayStation": ["PlayStation"],
    "Pokémon": ["Pokemon", "Pokémon"],
    "Naruto": ["Naruto"],
    "One Piece": ["One Piece"],
    "Dragon Ball": ["Dragon Ball"],
    "Hello Kitty": ["Hello Kitty", "Sanrio"],
    "Travis Scott": ["Travis Scott"],
    "Bad Bunny": ["Bad Bunny"],
    "Pharrell": ["Pharrell", "Pharrell Williams"],
    "J Balvin": ["J Balvin"],
    "Salehe Bembury": ["Salehe Bembury"],
    "G-Dragon": ["G-Dragon", "G Dragon", "GD", "PEACEMINUSONE"],
}

MODEL_BRAND_MAP: Dict[str, str] = {
    "Air Jordan": "Jordan",
    "Jordan": "Jordan",
    "AJ1": "Jordan", "AJ 1": "Jordan",
    "AJ3": "Jordan", "AJ 3": "Jordan",
    "AJ4": "Jordan", "AJ 4": "Jordan",
    "AJ5": "Jordan", "AJ 5": "Jordan",
    "AJ6": "Jordan", "AJ 6": "Jordan",
    "AJ11": "Jordan", "AJ 11": "Jordan",
    "Air Force 1": "Nike", "AF1": "Nike",
    "Dunk": "Nike",
    "Air Max": "Nike",
    "Vomero": "Nike",
    "Pegasus": "Nike",
    "Kobe": "Nike",
    "LeBron": "Nike",
    "KD": "Nike",
    "GT Cut": "Nike",
    "Foamposite": "Nike",
    "Blazer": "Nike",

    "Samba": "adidas",
    "Gazelle": "adidas",
    "Superstar": "adidas",
    "Campus": "adidas",
    "Stan Smith": "adidas",
    "UltraBoost": "adidas",
    "Forum": "adidas",
    "NMD": "adidas",
    "YEEZY": "adidas",

    "990": "New Balance",
    "991": "New Balance",
    "992": "New Balance",
    "993": "New Balance",
    "2002R": "New Balance",
    "1906R": "New Balance",
    "550": "New Balance",

    "GEL-Kayano": "ASICS",
    "GEL-Lyte": "ASICS",
    "GEL-Nimbus": "ASICS",
    "GEL": "ASICS",

    "XT-6": "Salomon",
    "XT-4": "Salomon",
    "ACS Pro": "Salomon",
    "Speedcross": "Salomon",
}

LUXURY_MODELS: Dict[str, str] = {
    "Triple S": "Balenciaga",
    "Cloudbust": "Prada",
    "LV Trainer": "Louis Vuitton",
}

BRAND_WEIGHT: Dict[str, int] = {
    "Nike": 100, "Jordan": 98, "adidas": 95, "New Balance": 85, "ASICS": 75, "Salomon": 70,
    "Louis Vuitton": 92, "Dior": 92, "Gucci": 90, "Balenciaga": 88, "Prada": 86,
}

LUXURY_OR_JEWELRY = {"Dior", "Louis Vuitton", "Gucci", "Balenciaga", "Prada", "Tiffany & Co.", "Swarovski", "Cartier", "Bvlgari", "Chrome Hearts"}
TOP_STREET_CULTURE = {"Travis Scott", "Off-White", "Supreme", "Stüssy", "KITH", "CLOT", "Fragment", "A Ma Maniére", "Aimé Leon Dore", "Bad Bunny", "Pharrell", "J Balvin", "Salehe Bembury", "G-Dragon"}


# =========================
# Data Structures
# =========================

@dataclass
class ShoeItem:
    source: str
    source_type: str  # "calendar" or "news"
    url: str
    shoes_name: str
    style_code: str
    release_date: str
    price: str
    brand_main: str
    brand_collab: List[str]
    priority: str  # S/A/B/C
    priority_score: int
    brand_weight: int
    release_days: Optional[int]
    detected_at: str


# =========================
# Utilities
# =========================

def now_taipei_iso() -> str:
    return datetime.now(TZ_TAIPEI).isoformat(timespec="seconds")

def safe_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def load_state() -> Dict[str, Any]:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def save_state(state: Dict[str, Any]) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

def http_get(url: str) -> str:
    r = requests.get(url, headers={"User-Agent": UA}, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.text

def normalize_title(s: str) -> str:
    s = safe_text(s)
    return s.replace("’", "'").replace("“", '"').replace("”", '"')


# =========================
# Brand / Collab detection
# =========================

def detect_brands(text: str) -> List[str]:
    text_l = (text or "").lower()
    found: List[str] = []
    for brand, aliases in BRAND_DICT.items():
        for a in aliases:
            if a.lower() in text_l:
                found.append(brand)
                break
    return sorted(set(found), key=lambda x: x.lower())

def detect_main_brand(title: str) -> str:
    t = (title or "").lower()

    for model, brand in LUXURY_MODELS.items():
        if model.lower() in t:
            return brand

    for model, brand in MODEL_BRAND_MAP.items():
        if model.lower() in t:
            if model.lower() == "yeezy":
                return "adidas"
            return brand

    brands = detect_brands(title)
    sports_priority = ["Nike", "Jordan", "adidas", "New Balance", "ASICS", "Salomon"]
    for b in sports_priority:
        if b in brands:
            return b

    return brands[0] if brands else "Unknown"

def is_collaboration(title: str, brands: List[str]) -> bool:
    t = (title or "").lower()
    if any(sig in t for sig in [" x ", "×", " & ", " collaboration", "collaboration", " collab"]):
        return True
    return len(brands) >= 2

def compute_release_days(release_date: str) -> Optional[int]:
    if not release_date or release_date.strip().lower() == "unknown":
        return None

    txt = release_date.strip()

    months = {
        "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
        "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
        "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10, "october": 10,
        "nov": 11, "november": 11, "dec": 12, "december": 12,
    }

    m = re.search(r"([A-Za-z]+)\s+(\d{1,2})(?:,\s*(\d{4}))?", txt)
    if not m:
        m = re.search(r"(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?", txt)
        if not m:
            return None
        mm = int(m.group(1))
        dd = int(m.group(2))
        yy = m.group(3)
        year = int(yy) if yy else datetime.now(TZ_TAIPEI).year
        if year < 100:
            year += 2000
        try:
            d = datetime(year, mm, dd, tzinfo=TZ_TAIPEI).date()
        except Exception:
            return None
    else:
        mon = months.get(m.group(1).lower())
        if not mon:
            return None
        day = int(m.group(2))
        year = int(m.group(3)) if m.group(3) else datetime.now(TZ_TAIPEI).year
        try:
            d = datetime(year, mon, day, tzinfo=TZ_TAIPEI).date()
        except Exception:
            return None
        if not m.group(3):
            today = datetime.now(TZ_TAIPEI).date()
            if d < today:
                d = datetime(year + 1, mon, day, tzinfo=TZ_TAIPEI).date()

    today = datetime.now(TZ_TAIPEI).date()
    return (d - today).days

def calc_priority_score(main_brand: str, collab: List[str], release_days: Optional[int]) -> Tuple[int, str]:
    score = 0
    score += BRAND_WEIGHT.get(main_brand, 35)

    if collab:
        score += 50
        if any(b in LUXURY_OR_JEWELRY for b in collab):
            score += 40
        if any(b in TOP_STREET_CULTURE for b in collab):
            score += 20

    if release_days is not None:
        if release_days <= 7:
            score += 10
        elif release_days <= 30:
            score += 5

    if score >= 160:
        level = "S"
    elif score >= 120:
        level = "A"
    elif score >= 85:
        level = "B"
    else:
        level = "C"
    return score, level

def build_fingerprint(item: ShoeItem) -> str:
    name = (item.shoes_name or "").lower()
    name = re.sub(r"[^a-z0-9\s\-']", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    style = (item.style_code or "").strip().upper()
    key = f"{item.brand_main}|{style or name}"
    return sha256(key)


# =========================
# Parsing helpers
# =========================

STYLE_CODE_RE = re.compile(r"\b[A-Z0-9]{2,6}\d{2,6}[-–][A-Z0-9]{2,6}\b|\b[A-Z]{1,3}\d{3,6}[-–]\d{3}\b|\b[A-Z]{1,3}\d{4,6}\b", re.I)
PRICE_RE = re.compile(r"\$\s?\d{2,5}(?:\.\d{2})?")
MONTH_WORDS = r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t|tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
DATE_RE = re.compile(rf"\b{MONTH_WORDS}\s+\d{{1,2}}(?:,\s*\d{{4}})?\b", re.I)
DATE_WITH_YEAR_RE = re.compile(rf"\b{MONTH_WORDS}\s+\d{{1,2}},\s*\d{{4}}\b", re.I)

def extract_fields_from_text(text: str) -> Tuple[str, str, str, str]:
    t = safe_text(text)

    style = "Unknown"
    m = STYLE_CODE_RE.search(t)
    if m:
        style = m.group(0).replace("–", "-").upper()

    price = "Unknown"
    mp = PRICE_RE.search(t)
    if mp:
        price = mp.group(0).replace(" ", "")

    rdate = "Unknown"
    md = DATE_RE.search(t)
    if md:
        rdate = md.group(0)

    cleaned = re.sub(r"(Style\s*Code|Release\s*Date|Retail\s*Price|Price)\s*:?\s*", " ", t, flags=re.I)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    shoes = cleaned
    if len(shoes) > 120:
        shoes = shoes[:120].rstrip() + "…"

    return shoes or "Unknown", style, rdate, price


# =========================
# Parsing: SneakerNews Calendar (IMPORTANT FIX)
# =========================

def parse_sneakernews_calendar(soup: BeautifulSoup, base_url: str, source_name: str) -> List[ShoeItem]:
    """
    SneakerNews /release-dates/ 是“鞋款条目”结构：
    - 鞋名在 h2/## 的链接里
    - 同一条目容器里包含 Retail Price / Style Code / 日期
    用结构化提取，避免把页面其他 a/li 文案当鞋名。
    """
    items: List[ShoeItem] = []

    # 页面里鞋名基本都在 h2 a[href]
    for a in soup.select("h2 a[href]"):
        title = normalize_title(a.get_text(" ").strip())
        if not title or len(title) < 4:
            continue

        href = a.get("href") or ""
        url = urljoin(base_url, href)

        # 找到该 h2 的“条目容器”，从里面抽字段
        h2 = a.find_parent("h2")
        container = None
        if h2:
            # 往上找一个较大的块（div/section/article）
            container = h2.find_parent(["article", "section", "div"])
        if container is None:
            container = h2 or a

        blob = safe_text(container.get_text(" "))

        # 日期：优先带年份的（January 27, 2026），找不到再用短日期
        release_date = "Unknown"
        mdy = DATE_WITH_YEAR_RE.search(blob)
        if mdy:
            release_date = mdy.group(0)
        else:
            md = DATE_RE.search(blob)
            if md:
                release_date = md.group(0)

        # Style Code：优先 “Style Code: XXX”
        style_code = "Unknown"
        m_sc = re.search(r"Style\s*Code:\s*([A-Z0-9\-–]{4,25})", blob, re.I)
        if m_sc:
            style_code = m_sc.group(1).replace("–", "-").upper()
        else:
            m = STYLE_CODE_RE.search(blob)
            if m:
                style_code = m.group(0).replace("–", "-").upper()

        # Price：优先 Retail Price
        price = "Unknown"
        m_pr = re.search(r"Retail\s*Price:\s*(\$\s?\d{2,5}(?:\.\d{2})?)", blob, re.I)
        if m_pr:
            price = m_pr.group(1).replace(" ", "")
        else:
            mp = PRICE_RE.search(blob)
            if mp:
                price = mp.group(0).replace(" ", "")

        main_brand = detect_main_brand(title)
        brands_all = detect_brands(title)
        collab: List[str] = []
        if is_collaboration(title, brands_all):
            collab = [b for b in brands_all if b != main_brand]

        # Yeezy 特判
        if "YEEZY" in brands_all:
            main_brand = "adidas"
            if "YEEZY" not in collab:
                collab.append("YEEZY")

        rd_days = compute_release_days(release_date)
        p_score, level = calc_priority_score(main_brand, collab, rd_days)

        items.append(ShoeItem(
            source=source_name,
            source_type="calendar",
            url=url,
            shoes_name=title,
            style_code=style_code,
            release_date=release_date,
            price=price,
            brand_main=main_brand,
            brand_collab=collab,
            priority=level,
            priority_score=p_score,
            brand_weight=BRAND_WEIGHT.get(main_brand, 35),
            release_days=rd_days,
            detected_at=now_taipei_iso(),
        ))

    # 排序并截断（优先级 > 品牌权重 > 发售临近）
    items = dedupe_items(items)
    items.sort(key=sort_key, reverse=True)
    return items[:MAX_PUSH_ITEMS_PER_RUN * 2]


# =========================
# Parsing: Generic Calendar (SBD 等)
# =========================

def parse_calendar_page(html: str, base_url: str, source_name: str) -> List[ShoeItem]:
    soup = BeautifulSoup(html, "html.parser")

    # ✅ SneakerNews calendar special parser
    if "sneakernews.com" in base_url and ("/release-dates" in base_url or "/air-jordan-release-dates" in base_url):
        return parse_sneakernews_calendar(soup, base_url, source_name)

    blocks: List[Tuple[str, str]] = []
    for a in soup.select("a[href]"):
        href = a.get("href") or ""
        text = safe_text(a.get_text(" "))
        if not text or len(text) < 6:
            continue
        if STYLE_CODE_RE.search(text) or any(b in text.lower() for b in ["nike", "jordan", "adidas", "new balance", "asics", "salomon", "puma", "vans"]):
            abs_url = urljoin(base_url, href)
            blocks.append((text, abs_url))

    for el in soup.select("li, tr, article, .post, .entry-content p"):
        txt = safe_text(el.get_text(" "))
        if not txt or len(txt) < 30:
            continue
        if not (STYLE_CODE_RE.search(txt) or DATE_RE.search(txt) or PRICE_RE.search(txt)):
            continue
        blocks.append((txt, base_url))

    seen_block = set()
    uniq_blocks = []
    for t, u in blocks:
        h = sha256(t[:300])
        if h in seen_block:
            continue
        seen_block.add(h)
        uniq_blocks.append((t, u))

    items: List[ShoeItem] = []
    for t, u in uniq_blocks:
        shoes_name, style_code, release_date, price = extract_fields_from_text(t)
        shoes_name = normalize_title(shoes_name)

        if (not shoes_name or shoes_name.lower() == "unknown") and (style_code == "Unknown"):
            continue

        main_brand = detect_main_brand(shoes_name)
        brands_all = detect_brands(shoes_name)
        collab: List[str] = []
        if is_collaboration(shoes_name, brands_all):
            collab = [b for b in brands_all if b != main_brand]

        if "YEEZY" in brands_all:
            main_brand = "adidas"
            if "YEEZY" not in collab:
                collab.append("YEEZY")

        rd_days = compute_release_days(release_date)
        p_score, level = calc_priority_score(main_brand, collab, rd_days)

        items.append(ShoeItem(
            source=source_name,
            source_type="calendar",
            url=u,
            shoes_name=shoes_name,
            style_code=style_code or "Unknown",
            release_date=release_date or "Unknown",
            price=price or "Unknown",
            brand_main=main_brand,
            brand_collab=collab,
            priority=level,
            priority_score=p_score,
            brand_weight=BRAND_WEIGHT.get(main_brand, 35),
            release_days=rd_days,
            detected_at=now_taipei_iso(),
        ))

    items = dedupe_items(items)
    items.sort(key=sort_key, reverse=True)
    return items[:MAX_PUSH_ITEMS_PER_RUN * 2]


# =========================
# Parsing: News
# =========================

def extract_article_links(html: str, base_url: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    links: List[str] = []
    for a in soup.select("a[href]"):
        href = a.get("href") or ""
        if not href:
            continue
        abs_url = urljoin(base_url, href)
        p = urlparse(abs_url)
        if p.scheme not in ("http", "https"):
            continue
        if any(x in abs_url for x in ["/category/", "/tag/", "/page/", "/author/", "/wp-admin", "/wp-content", "#", "mailto:"]):
            continue

        if "sneakerbardetroit.com" in p.netloc:
            if p.path.count("/") >= 2 and p.path.endswith("/"):
                links.append(abs_url)
        elif "sneakernews.com" in p.netloc:
            if re.search(r"/\d{4}/\d{2}/\d{2}/", p.path):
                links.append(abs_url)

    seen = set()
    out = []
    for u in links:
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out[:50]

def parse_article(html: str, url: str, source_name: str) -> Optional[ShoeItem]:
    soup = BeautifulSoup(html, "html.parser")

    title = ""
    h1 = soup.find("h1")
    if h1:
        title = safe_text(h1.get_text(" "))
    if not title:
        title = safe_text(soup.title.get_text(" ")) if soup.title else ""
    title = normalize_title(title) or "Unknown"

    # body containers (尽量只取文章主体)
    body = ""
    containers = []
    for sel in ["article", ".entry-content", ".post-content", ".single-post", "main"]:
        containers.extend(soup.select(sel))
    if containers:
        body = safe_text(" ".join(c.get_text(" ") for c in containers[:2]))
    else:
        body = safe_text(soup.get_text(" "))

    shoes_name = title
    style_code = "Unknown"
    release_date = "Unknown"
    price = "Unknown"

    labeled = soup.get_text("\n")

    m_sc = re.search(r"(Style\s*Code|SKU)\s*[:\-]\s*([A-Z0-9\-–]{6,25})", labeled, re.I)
    if m_sc:
        style_code = m_sc.group(2).replace("–", "-").upper()
    else:
        m = STYLE_CODE_RE.search(labeled)
        if m:
            style_code = m.group(0).replace("–", "-").upper()

    m_pr = re.search(r"(Price|Retail)\s*[:\-]\s*(\$\s?\d{2,5}(?:\.\d{2})?)", labeled, re.I)
    if m_pr:
        price = m_pr.group(2).replace(" ", "")
    else:
        mp = PRICE_RE.search(labeled)
        if mp:
            price = mp.group(0).replace(" ", "")

    m_dt = re.search(r"(Release\s*Date|Dropping|Launch(?:es)?)\s*[:\-]\s*(" + MONTH_WORDS + r"\s+\d{1,2}(?:,\s*\d{4})?)", labeled, re.I)
    if m_dt:
        release_date = m_dt.group(2)
    else:
        md = DATE_RE.search(labeled)
        if md:
            release_date = md.group(0)

    main_brand = detect_main_brand(shoes_name)

    # ✅ 关键修正：brands/collab 只看标题（避免 SneakerNews 页眉/介绍文案污染）
    brands_all = detect_brands(shoes_name)

    sneakerish = (main_brand != "Unknown") or (style_code != "Unknown") or (price != "Unknown") or (release_date != "Unknown")
    if not sneakerish:
        return None

    collab: List[str] = []
    if is_collaboration(shoes_name, brands_all):
        collab = [b for b in brands_all if b != main_brand]

    # Off-White 自主品牌特判（你之前的逻辑保留）
    if "Off-White" in brands_all and main_brand in ("Nike", "Unknown"):
        nike_model_signals = ["air force", "af1", "dunk", "air max", "jordan", "kobe", "vomero", "pegasus"]
        if not any(sig in shoes_name.lower() for sig in nike_model_signals):
            main_brand = "Off-White"
            collab = [b for b in brands_all if b != main_brand]

    if "YEEZY" in brands_all:
        main_brand = "adidas"
        if "YEEZY" not in collab:
            collab.append("YEEZY")

    rd_days = compute_release_days(release_date)
    p_score, level = calc_priority_score(main_brand, collab, rd_days)

    return ShoeItem(
        source=source_name,
        source_type="news",
        url=url,
        shoes_name=shoes_name,
        style_code=style_code or "Unknown",
        release_date=release_date or "Unknown",
        price=price or "Unknown",
        brand_main=main_brand,
        brand_collab=collab,
        priority=level,
        priority_score=p_score,
        brand_weight=BRAND_WEIGHT.get(main_brand, 35),
        release_days=rd_days,
        detected_at=now_taipei_iso(),
    )


# =========================
# Dedupe + Sorting
# =========================

def dedupe_items(items: List[ShoeItem]) -> List[ShoeItem]:
    best: Dict[str, ShoeItem] = {}
    for it in items:
        fp = build_fingerprint(it)
        if fp not in best:
            best[fp] = it
        else:
            # keep higher score version
            if it.priority_score > best[fp].priority_score:
                best[fp] = it
    return list(best.values())

def sort_key(it: ShoeItem) -> Tuple[int, int, int]:
    # Priority > BrandWeight > ReleaseDate(closer is better)
    pr_map = {"S": 4, "A": 3, "B": 2, "C": 1}
    pr = pr_map.get(it.priority, 0)
    bw = it.brand_weight or 0
    # closer -> higher score
    if it.release_days is None:
        rs = 0
    else:
        rs = 9999 - max(min(it.release_days, 9999), -9999)
    return (pr, bw, rs)


# =========================
# Push (Telegram / WeCom / GitHub Issue Comment)
# =========================

def format_item(it: ShoeItem) -> str:
    is_collab = "Collaboration" if it.brand_collab else "General"
    header = f""

    lines = [
        header,
        "",
        f"Shoes Name: {it.shoes_name}",
        f"Style Code: {it.style_code}",
        f"Release Date: {it.release_date}",
        f"Price: {it.price}",
        "",
        f"Brand_Main: {it.brand_main}",
    ]
    if it.brand_collab:
        lines.append(f"Brand_Collab: {', '.join(it.brand_collab)}")
    lines.extend([
        f"Source: {it.source_type} | {it.source}",
        f"URL: {it.url}",
    ])
    return "\n".join(lines)

def send_telegram(text: str) -> bool:
    token = os.getenv("TG_BOT_TOKEN") or ""
    chat_id = os.getenv("TG_CHAT_ID") or ""
    if not token or not chat_id:
        return False
    api = f"https://api.telegram.org/bot{token}/sendMessage"
    r = requests.post(api, json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True}, timeout=REQUEST_TIMEOUT)
    return r.ok

def send_wecom(text: str) -> bool:
    webhook = os.getenv("WECOM_WEBHOOK") or os.getenv("WECOM_WEBHOOK_URL") or ""
    if not webhook:
        return False
    payload = {"msgtype": "text", "text": {"content": text}}
    r = requests.post(webhook, json=payload, timeout=REQUEST_TIMEOUT)
    return r.ok

def github_issue_comment(text: str) -> bool:
    token = os.getenv("GITHUB_TOKEN") or ""
    repo = os.getenv("GITHUB_REPOSITORY") or ""
    issue_number = os.getenv("ISSUE_NUMBER") or ""
    if not token or not repo or not issue_number:
        return False
    api = f"https://api.github.com/repos/{repo}/issues/{issue_number}/comments"
    r = requests.post(
        api,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        json={"body": text},
        timeout=REQUEST_TIMEOUT,
    )
    return r.ok

def push_text(text: str) -> None:
    # Try Telegram / WeCom; GitHub Issue as fallback/also
    tg_ok = send_telegram(text)
    wc_ok = send_wecom(text)
    gh_ok = github_issue_comment(text)
    print(f"[push] telegram={tg_ok} wecom={wc_ok} github_issue_comment={gh_ok}")


# =========================
# Main
# =========================

def collect_calendar_items() -> List[ShoeItem]:
    all_items: List[ShoeItem] = []
    for src in CALENDAR_SITES:
        try:
            html = http_get(src["url"])
            items = parse_calendar_page(html, src["url"], src["name"])
            all_items.extend(items[:MAX_PUSH_ITEMS_PER_RUN * 2])
        except Exception as e:
            print(f"[calendar] failed: {src['url']} err={e}")
    return all_items

def collect_news_items() -> List[ShoeItem]:
    all_items: List[ShoeItem] = []
    for src in NEWS_SOURCES:
        try:
            html = http_get(src["url"])
            links = extract_article_links(html, src["url"])
            # limit per source
            links = links[:MAX_ARTICLES_PER_SOURCE]
            for u in links:
                try:
                    ahtml = http_get(u)
                    item = parse_article(ahtml, u, src["name"])
                    if item:
                        all_items.append(item)
                except Exception as e2:
                    print(f"[news] article failed: {u} err={e2}")
        except Exception as e:
            print(f"[news] failed: {src['url']} err={e}")
    return all_items

def main():
    state = load_state()
    sent: Dict[str, Any] = state.get("sent", {})  # fingerprint -> last_sent_iso

    items = []
    items.extend(collect_calendar_items())
    items.extend(collect_news_items())

    # final dedupe + sort
    items = dedupe_items(items)
    items.sort(key=sort_key, reverse=True)

    # filter new (dedupe by fingerprint)
    new_items: List[ShoeItem] = []
    for it in items:
        fp = build_fingerprint(it)
        if fp in sent:
            continue
        new_items.append(it)

    # cap push per run
    new_items = new_items[:MAX_PUSH_ITEMS_PER_RUN]

    if not new_items:
        print("No new items.")
        return

    # push one-by-one (safer for telegram length)
    pushed = 0
    for it in new_items:
        text = format_item(it)
        push_text(text)
        fp = build_fingerprint(it)
        sent[fp] = now_taipei_iso()
        pushed += 1
        time.sleep(0.8)

    state["sent"] = sent
    state["last_run"] = now_taipei_iso()
    state["pushed_last_run"] = pushed
    save_state(state)

    print(f"Pushed {pushed} items.")

if __name__ == "__main__":
    main()
