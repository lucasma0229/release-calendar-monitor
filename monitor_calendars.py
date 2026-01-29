import os
import re
import json
import time
import hashlib
from dataclasses import dataclass
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

# 100+ brand dictionary (aliases). You can extend safely.
BRAND_DICT: Dict[str, List[str]] = {
    # --- Sports (Global) ---
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
    "Lotto": ["Lotto"],
    "Umbro": ["Umbro"],
    "Skechers": ["Skechers"],
    "Timberland": ["Timberland"],
    "The North Face": ["The North Face", "TNF"],
    "Arc'teryx": ["Arc'teryx", "Arcteryx"],
    "Columbia": ["Columbia"],
    "Nike SB": ["Nike SB", "SB"],
    "YEEZY": ["YEEZY", "Yeezy"],

    # --- Streetwear / labels ---
    "Supreme": ["Supreme"],
    "Stüssy": ["Stussy", "Stüssy"],
    "BAPE": ["BAPE", "A Bathing Ape"],
    "CLOT": ["CLOT"],
    "KITH": ["Kith", "KITH"],
    "Off-White": ["Off-White", "Off White"],
    "Fragment": ["Fragment", "fragment design"],
    "AMBUSH": ["AMBUSH"],
    "Palace": ["Palace"],
    "Fear of God": ["Fear of God", "FOG"],
    "A-COLD-WALL*": ["A-COLD-WALL", "A-COLD-WALL*"],
    "Human Made": ["Human Made"],
    "WTAPS": ["WTAPS"],
    "NEIGHBORHOOD": ["NEIGHBORHOOD", "Neighborhood"],
    "Rhude": ["Rhude"],
    "UNDEFEATED": ["UNDEFEATED"],
    "Patta": ["Patta"],
    "Noah": ["Noah"],
    "Aimé Leon Dore": ["Aimé Leon Dore", "Aime Leon Dore", "ALD"],
    "JJJJound": ["JJJJound"],
    "Stone Island": ["Stone Island"],
    "Comme des Garçons": ["Comme des Garçons", "Comme des Garcons", "CDG"],
    "Bodega": ["Bodega"],
    "Concepts": ["Concepts"],
    "SNS": ["Sneakersnstuff", "SNS"],
    "END.": ["END.", "END Clothing"],
    "size?": ["size?"],
    "atmos": ["atmos"],
    "Union": ["Union"],
    "A Ma Maniére": ["A Ma Maniére", "A Ma Maniere", "AMM"],

    # --- Luxury ---
    "Dior": ["Dior"],
    "Louis Vuitton": ["Louis Vuitton", "LV"],
    "Gucci": ["Gucci"],
    "Balenciaga": ["Balenciaga"],
    "Prada": ["Prada"],
    "Givenchy": ["Givenchy"],
    "Saint Laurent": ["Saint Laurent", "YSL"],
    "Alexander McQueen": ["Alexander McQueen", "McQueen"],
    "Maison Margiela": ["Maison Margiela", "Margiela"],
    "Rick Owens": ["Rick Owens"],
    "Versace": ["Versace"],
    "Burberry": ["Burberry"],
    "Ferragamo": ["Ferragamo", "Salvatore Ferragamo"],
    "Loewe": ["Loewe"],
    "Celine": ["Celine", "Céline"],
    "Bottega Veneta": ["Bottega Veneta"],
    "Moncler": ["Moncler"],

    # --- Jewelry / culture / IP ---
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
    "LEGO": ["LEGO"],
    "Hello Kitty": ["Hello Kitty", "Sanrio"],
    "Travis Scott": ["Travis Scott"],
    "Bad Bunny": ["Bad Bunny"],
    "Pharrell": ["Pharrell", "Pharrell Williams"],
    "J Balvin": ["J Balvin"],
    "Salehe Bembury": ["Salehe Bembury"],
    "KAWS": ["KAWS"],
    "Takashi Murakami": ["Takashi Murakami", "Murakami"],
    "G-Dragon": ["G-Dragon", "GD", "PEACEMINUSONE"],
}

BRAND_CATEGORY: Dict[str, str] = {
    # sports
    "Nike": CAT_SPORTS, "Jordan": CAT_SPORTS, "adidas": CAT_SPORTS, "New Balance": CAT_SPORTS,
    "ASICS": CAT_SPORTS, "Puma": CAT_SPORTS, "Converse": CAT_SPORTS, "Reebok": CAT_SPORTS,
    "Vans": CAT_SPORTS, "Under Armour": CAT_SPORTS, "Mizuno": CAT_SPORTS, "Brooks": CAT_SPORTS,
    "On": CAT_SPORTS, "HOKA": CAT_SPORTS, "Salomon": CAT_SPORTS, "Saucony": CAT_SPORTS,
    "Li-Ning": CAT_SPORTS, "ANTA": CAT_SPORTS, "361°": CAT_SPORTS, "Xtep": CAT_SPORTS,
    "PEAK": CAT_SPORTS, "Descente": CAT_SPORTS, "Fila": CAT_SPORTS, "Kappa": CAT_SPORTS,
    "YEEZY": CAT_CULTURE,

    # streetwear
    "Supreme": CAT_STREET, "Stüssy": CAT_STREET, "BAPE": CAT_STREET, "CLOT": CAT_STREET,
    "KITH": CAT_STREET, "Off-White": CAT_STREET, "Fragment": CAT_STREET, "AMBUSH": CAT_STREET,
    "Palace": CAT_STREET, "Fear of God": CAT_STREET, "A-COLD-WALL*": CAT_STREET,
    "Human Made": CAT_STREET, "WTAPS": CAT_STREET, "NEIGHBORHOOD": CAT_STREET, "Rhude": CAT_STREET,
    "UNDEFEATED": CAT_STREET, "Patta": CAT_STREET, "Noah": CAT_STREET, "Aimé Leon Dore": CAT_STREET,
    "JJJJound": CAT_STREET, "Stone Island": CAT_STREET, "Comme des Garçons": CAT_STREET,

    # luxury
    "Dior": CAT_LUXURY, "Louis Vuitton": CAT_LUXURY, "Gucci": CAT_LUXURY, "Balenciaga": CAT_LUXURY,
    "Prada": CAT_LUXURY, "Givenchy": CAT_LUXURY, "Saint Laurent": CAT_LUXURY, "Alexander McQueen": CAT_LUXURY,
    "Maison Margiela": CAT_LUXURY, "Rick Owens": CAT_LUXURY, "Versace": CAT_LUXURY, "Burberry": CAT_LUXURY,
    "Ferragamo": CAT_LUXURY, "Loewe": CAT_LUXURY, "Celine": CAT_LUXURY, "Bottega Veneta": CAT_LUXURY,
    "Moncler": CAT_LUXURY,

    # jewelry / ip / culture
    "Tiffany & Co.": CAT_JEWELRY, "Swarovski": CAT_JEWELRY, "Cartier": CAT_JEWELRY,
    "Bvlgari": CAT_JEWELRY, "Chrome Hearts": CAT_JEWELRY,
    "Disney": CAT_IP, "Marvel": CAT_IP, "Star Wars": CAT_IP, "NBA": CAT_IP, "NFL": CAT_IP,
    "PlayStation": CAT_IP, "Pokémon": CAT_IP, "Naruto": CAT_IP, "One Piece": CAT_IP, "Dragon Ball": CAT_IP,
    "LEGO": CAT_IP, "Hello Kitty": CAT_IP,
    "Travis Scott": CAT_CULTURE, "Bad Bunny": CAT_CULTURE, "Pharrell": CAT_CULTURE, "J Balvin": CAT_CULTURE,
    "Salehe Bembury": CAT_CULTURE, "KAWS": CAT_CULTURE, "Takashi Murakami": CAT_CULTURE, "G-Dragon": CAT_CULTURE,
}

MODEL_BRAND_MAP: Dict[str, str] = {
    # Jordan / Nike
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
    "Cortez": "Nike",
    "Vomero": "Nike",
    "Pegasus": "Nike",
    "Kobe": "Nike",
    "LeBron": "Nike",
    "KD": "Nike",
    "GT Cut": "Nike",
    "Foamposite": "Nike",
    "Blazer": "Nike",

    # adidas
    "Samba": "adidas",
    "Gazelle": "adidas",
    "Superstar": "adidas",
    "Campus": "adidas",
    "Stan Smith": "adidas",
    "UltraBoost": "adidas",
    "Forum": "adidas",
    "NMD": "adidas",
    "YEEZY": "adidas",
    "Boost": "adidas",

    # New Balance
    "990": "New Balance",
    "991": "New Balance",
    "992": "New Balance",
    "993": "New Balance",
    "2002R": "New Balance",
    "1906R": "New Balance",
    "550": "New Balance",

    # ASICS
    "GEL-Kayano": "ASICS",
    "GEL-Lyte": "ASICS",
    "GEL-Nimbus": "ASICS",
    "GEL": "ASICS",

    # Salomon
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
    "HOKA": 62, "Puma": 60, "Vans": 58, "Converse": 55,
    "Li-Ning": 52, "ANTA": 48, "361°": 45, "Fila": 42,
    "Louis Vuitton": 92, "Dior": 92, "Gucci": 90, "Balenciaga": 88, "Prada": 86,
}

LUXURY_OR_JEWELRY = {
    "Dior", "Louis Vuitton", "Gucci", "Balenciaga", "Prada",
    "Tiffany & Co.", "Swarovski", "Cartier", "Bvlgari", "Chrome Hearts"
}
TOP_STREET_CULTURE = {
    "Travis Scott", "Off-White", "Supreme", "Stüssy", "KITH", "CLOT", "Fragment",
    "A Ma Maniére", "Aimé Leon Dore", "Bad Bunny", "Pharrell", "J Balvin",
    "Salehe Bembury", "G-Dragon"
}

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
    s = s.replace("’", "'").replace("“", '"').replace("”", '"')
    return s

# =========================
# Brand / Collab detection
# =========================

# ---- brand detect (safer) ----
_SHORT_ALIAS = {"on", "ua", "sb", "nb", "lv", "gd"}  # 可按需增减

def _alias_pattern(alias: str) -> re.Pattern:
    a = alias.strip()
    a_norm = re.escape(a)
    # 对很短的 alias 强制词边界，避免误命中
    if len(a) <= 3 or a.lower() in _SHORT_ALIAS:
        return re.compile(rf"(?<![a-z0-9]){a_norm}(?![a-z0-9])", re.I)
    return re.compile(a_norm, re.I)

# 预编译，提高速度
_ALIAS_PATTERNS: Dict[str, List[re.Pattern]] = {
    brand: [_alias_pattern(a) for a in aliases]
    for brand, aliases in BRAND_DICT.items()
}

def detect_brands(text: str) -> List[str]:
    txt = text or ""
    found: List[str] = []
    for brand, patterns in _ALIAS_PATTERNS.items():
        for pat in patterns:
            if pat.search(txt):
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
    sports_priority = ["Nike", "Jordan", "adidas", "New Balance", "ASICS", "Salomon", "HOKA", "Puma", "Vans", "Converse", "Li-Ning", "ANTA", "361°", "Fila"]
    for b in sports_priority:
        if b in brands:
            return b

    return brands[0] if brands else "Unknown"

COLLAB_SIGNALS = [
    " x ", "×", " & ",
    " collaboration", " collab", " collab.",
    " in partnership with", " partnered with", " co-branded",
    " by ",  # 保守信号：只在 strong 白名单命中时才生效（见下）
]

def is_collaboration(title: str, brands: List[str], main_brand: str) -> bool:
    """
    更严格的联名判定：
    - 不能再用 len(brands) >= 2 这种“噪音必爆”的规则
    - 需要：结构信号（x/×/&/collab等） 或 强联名白名单命中
    - 并过滤 Jordan<->Nike 的“母品牌关系”误判
    """
    t = (title or "").lower()
    brands = brands or []

    # 过滤母品牌误判：Jordan 的 Nike 不算联名（反向也做一下）
    filtered = [b for b in brands if b != main_brand]
    if main_brand == "Jordan":
        filtered = [b for b in filtered if b != "Nike"]
    if main_brand == "Nike":
        filtered = [b for b in filtered if b != "Jordan"]

    # 强联名白名单：这些出现时，即使没有明显 x，也可能是联名/合作
    strong = any(b in TOP_STREET_CULTURE or b in LUXURY_OR_JEWELRY for b in filtered)

    # 结构信号：标题里出现 x/×/&/collab 等
    has_signal = any(sig in t for sig in COLLAB_SIGNALS)

    # “by” 这种信号太宽，只有 strong 命中时才认可
    if " by " in t and not strong:
        has_signal = any(sig in t for sig in [" x ", "×", " & ", " collaboration", " collab", " collab."])

    return (has_signal and len(filtered) >= 1) or strong

# =========================
# Date / Price / Style patterns
# =========================

STYLE_CODE_RE = re.compile(r"\b[A-Z0-9]{2,6}\d{2,6}[-–][A-Z0-9]{2,6}\b|\b[A-Z]{1,3}\d{3,6}[-–]\d{3}\b", re.I)
PRICE_RE = re.compile(r"\$\s?\d{2,5}(?:\.\d{2})?")

MONTH_WORDS = r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t|tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
DATE_RE = re.compile(rf"\b{MONTH_WORDS}\s+\d{{1,2}}(?:,\s*\d{{4}})?\b", re.I)

def compute_release_days(release_date: str) -> Optional[int]:
    if not release_date or release_date.strip().lower() == "unknown":
        return None

    txt = release_date.strip()
    months = {
        "jan": 1, "january": 1,
        "feb": 2, "february": 2,
        "mar": 3, "march": 3,
        "apr": 4, "april": 4,
        "may": 5,
        "jun": 6, "june": 6,
        "jul": 7, "july": 7,
        "aug": 8, "august": 8,
        "sep": 9, "sept": 9, "september": 9,
        "oct": 10, "october": 10,
        "nov": 11, "november": 11,
        "dec": 12, "december": 12,
    }

    m = re.search(r"([A-Za-z]+)\s+(\d{1,2})(?:,\s*(\d{4}))?", txt)
    if not m:
        m2 = re.search(r"(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?", txt)
        if not m2:
            return None
        mm = int(m2.group(1))
        dd = int(m2.group(2))
        yy = m2.group(3)
        if yy:
            year = int(yy)
            if year < 100:
                year += 2000
        else:
            year = datetime.now(TZ_TAIPEI).year
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
                try:
                    d = datetime(year + 1, mon, day, tzinfo=TZ_TAIPEI).date()
                except Exception:
                    pass

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
# SneakerNews title cleaning (核心修复点)
# =========================

_PREFIX_CLEAN_RE = re.compile(
    r"^(Official Images Of( The)?|First Look( At)?( The)?|Detailed Look( At)?( The)?|"
    r"An Official Look At( The)?|Best Look Yet At( The)?|"
    r"Photos Of( The)?|Look For( The)?|"
    r"Here's( The)?|"
    r"Where To Buy( The)?|"
    r"Release Date For( The)?|"
    r""
    r")\s+",
    re.I
)

# 常见“尾巴”关键词：一旦出现，优先截断（保留前面的鞋名主体）
_TAIL_CUT_KEYWORDS = [
    " releases", " releasing", " drops", " dropping", " launches", " launching",
    " arrives", " arriving", " officially", " official images", " first look",
    " revealed", " revealed in", " revealed at", " surfaces", " appears",
    " on the way", " set to", " coming", " returns", " return",
    " gets", " get", " for", " in paris", " in tokyo", " in",
]

# 试图在标题里抽“像鞋名的片段”
# 例：Official Images Of The Women's Nike Air Max 95 OG "Neon"
_SHOE_LIKE_RE = re.compile(
    r"(?P<shoe>(Nike|Air Jordan|Jordan|adidas|New Balance|ASICS|Salomon|Puma|Vans|Converse)\b.*?)(?:$|\s+(Releases|Launches|Drops|Dropping|Arrives|Revealed|Official|First|Where|Release))",
    re.I
)

def clean_shoes_name_from_title(title: str) -> str:
    t = normalize_title(title)

    # 1) 去掉常见前缀
    t2 = _PREFIX_CLEAN_RE.sub("", t).strip()

    # 2) 去掉“Women’s / Men's / The / A”等对鞋名无贡献的前置词（保守做）
    t2 = re.sub(r"^(The|A|An)\s+", "", t2, flags=re.I).strip()
    t2 = re.sub(r"^(Women'?s|Men'?s|Kid'?s|GS)\s+", "", t2, flags=re.I).strip()

    # 3) 尝试从中间抽鞋名片段（对 SneakerNews 很关键）
    m = _SHOE_LIKE_RE.search(t2)
    if m:
        candidate = m.group("shoe").strip()
    else:
        candidate = t2

    # 4) 按“尾巴关键词”截断（Releases / Revealed / etc）
    cand_l = candidate.lower()
    cut_pos = None
    for kw in _TAIL_CUT_KEYWORDS:
        idx = cand_l.find(kw)
        if idx != -1:
            cut_pos = idx if cut_pos is None else min(cut_pos, idx)
    if cut_pos is not None and cut_pos > 0:
        candidate = candidate[:cut_pos].strip()

    # 5) 再清一次：去掉多余结尾标点
    candidate = candidate.strip(" -–|:•·").strip()
    candidate = re.sub(r"\s+", " ", candidate).strip()

    # 6) 太短就回退原标题（避免抽坏）
    if len(candidate) < 8:
        return normalize_title(title)

    return candidate

# =========================
# Category / Tag (按你模板输出)
# =========================

def build_tag(main_brand: str, collab: List[str]) -> str:
    if collab:
        parts = [main_brand] + collab
        # 使用 “×” 连接；多方联名也适配
        return " × ".join(parts)
    return main_brand if main_brand != "Unknown" else "Unknown"

def build_category(main_brand: str, collab: List[str]) -> str:
    if not collab:
        return "General Release"

    cats = []
    for b in collab:
        cats.append(BRAND_CATEGORY.get(b, CAT_CULTURE))

    # 规则：只要有珠宝 > 奢侈 > IP > 街头 > 运动
    if any(c == CAT_JEWELRY for c in cats):
        return "Jewelry Collaboration"
    if any(c == CAT_LUXURY for c in cats):
        return "Luxury Collaboration"
    if any(c == CAT_IP for c in cats):
        return "IP Collaboration"
    if any(c == CAT_STREET for c in cats):
        return "Streetwear Collaboration"
    return "Sports Collaboration"

# =========================
# Parsing: Calendar (SBD/SN release-dates pages)
# =========================

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

    cleaned = re.sub(r"(Style\s*Code|Release\s*Date|Price)\s*:?\s*", " ", t, flags=re.I)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    shoes = cleaned
    if len(shoes) > 120:
        shoes = shoes[:120].rstrip() + "…"

    return shoes or "Unknown", style, rdate, price

def parse_calendar_page(html: str, base_url: str, source_name: str) -> List[ShoeItem]:
    soup = BeautifulSoup(html, "html.parser")

    blocks: List[Tuple[str, str]] = []
    for a in soup.select("a"):
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
        collab = []
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

    items.sort(key=lambda x: (priority_rank(x.priority), x.brand_weight, -(9999 if x.release_days is None else (9999 - max(0, x.release_days)))), reverse=True)
    return items[:MAX_PUSH_ITEMS_PER_RUN * 2]

# =========================
# Parsing: News (SBD/SN latest pages -> article -> fields)
# =========================

def extract_article_links(html: str, base_url: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    links = []
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

    # title
    title = ""
    h1 = soup.find("h1")
    if h1:
        title = safe_text(h1.get_text(" "))
    if not title and soup.title:
        title = safe_text(soup.title.get_text(" "))
    title = normalize_title(title) or "Unknown"

    # body text (for brand/collab signals)
    containers = []
    for sel in ["article", ".entry-content", ".post-content", ".single-post", ".post", "main"]:
        containers.extend(soup.select(sel))
    if containers:
        body = safe_text(" ".join(c.get_text(" ") for c in containers[:2]))
    else:
        body = safe_text(soup.get_text(" "))

    labeled = soup.get_text("\n")

    # SneakerNews title cleaning (only apply to SneakerNews)
    shoes_name = title
    if "sneakernews.com" in urlparse(url).netloc:
        shoes_name = clean_shoes_name_from_title(title)

    style_code = "Unknown"
    m_sc = re.search(r"(Style\s*Code|SKU)\s*[:\-]\s*([A-Z0-9\-–]{6,20})", labeled, re.I)
    if m_sc:
        style_code = m_sc.group(2).replace("–", "-").upper()
    else:
        m = STYLE_CODE_RE.search(labeled)
        if m:
            style_code = m.group(0).replace("–", "-").upper()

    price = "Unknown"
    m_pr = re.search(r"(Price|Retail)\s*[:\-]\s*(\$\s?\d{2,5}(?:\.\d{2})?)", labeled, re.I)
    if m_pr:
        price = m_pr.group(2).replace(" ", "")
    else:
        mp = PRICE_RE.search(labeled)
        if mp:
            price = mp.group(0).replace(" ", "")

    release_date = "Unknown"
    m_dt = re.search(r"(Release\s*Date|Dropping|Launch(?:es)?)\s*[:\-]\s*(" + MONTH_WORDS + r"\s+\d{1,2}(?:,\s*\d{4})?)", labeled, re.I)
    if m_dt:
        release_date = m_dt.group(2)
    else:
        md = DATE_RE.search(labeled)
        if md:
            release_date = md.group(0)

    # Determine brands/collab
    main_brand = detect_main_brand(shoes_name)
    brands_all = detect_brands(shoes_name)

    # If title has no strong sports model but has Off-White, allow Off-White as main
    if "Off-White" in brands_all and main_brand in ("Nike", "Unknown"):
        nike_model_signals = ["air force", "af1", "dunk", "air max", "jordan", "kobe", "vomero", "pegasus"]
        if not any(sig in shoes_name.lower() for sig in nike_model_signals):
            main_brand = "Off-White"

    collab: List[str] = []
    if is_collaboration(shoes_name, brands_all):
        collab = [b for b in brands_all if b != main_brand]

    # Yeezy special
    if "YEEZY" in brands_all:
        main_brand = "adidas"
        if "YEEZY" not in collab:
            collab.append("YEEZY")

    # Filter: ensure sneaker-ish
    sneakerish = (main_brand != "Unknown") or (style_code != "Unknown") or (price != "Unknown") or (release_date != "Unknown")
    if not sneakerish:
        return None

    rd_days = compute_release_days(release_date)
    p_score, level = calc_priority_score(main_brand, collab, rd_days)

    return ShoeItem(
        source=source_name,
        source_type="news",
        url=url,
        shoes_name=normalize_title(shoes_name),
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
# Sorting / Priority
# =========================

def priority_rank(p: str) -> int:
    return {"S": 4, "A": 3, "B": 2, "C": 1}.get((p or "").strip().upper(), 0)

def sort_items(items: List[ShoeItem]) -> List[ShoeItem]:
    # Priority > BrandWeight > ReleaseDate(nearer first; unknown last)
    def key(x: ShoeItem):
        rd = x.release_days
        rd_sort = 999999 if rd is None else max(0, rd)
        return (priority_rank(x.priority), x.brand_weight, -x.priority_score, -1 * (999999 - rd_sort))
    return sorted(items, key=key, reverse=True)

# =========================
# Push formatting (你要的模板)
# =========================

def format_push_message(item: ShoeItem) -> str:
    collab_flag = " · Collaboration" if item.brand_collab else ""
    header = f""

    tag = build_tag(item.brand_main, item.brand_collab)
    category = build_category(item.brand_main, item.brand_collab)

    # 字段之间不留空行
    lines = [
        header,
        f"Shoes：{item.shoes_name}",
        f"Style Code: {item.style_code}",
        f"Release Date: {item.release_date}",
        f"Price: {item.price}",
        f"Tag：{tag}",
        f"Category：{category}",
        f"Priority：{item.priority}",
    ]
    return "\n".join(lines)

# =========================
# Push: Telegram / WeCom / GitHub Issue comment
# =========================

def send_telegram(bot_token: str, chat_id: str, text: str) -> None:
    if not bot_token or not chat_id:
        return
    api = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    r = requests.post(api, json=payload, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()

def send_wecom(webhook_url: str, text: str) -> None:
    if not webhook_url:
        return
    payload = {"msgtype": "text", "text": {"content": text}}
    r = requests.post(webhook_url, json=payload, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()

def post_github_issue_comment(token: str, repo: str, issue_number: str, body: str) -> None:
    if not token or not repo or not issue_number:
        return
    api = f"https://api.github.com/repos/{repo}/issues/{issue_number}/comments"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "ColdTreasure-Monitor",
    }
    r = requests.post(api, headers=headers, json={"body": body}, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()

# =========================
# Dedupe / State maintenance
# =========================

def prune_sent(sent: Dict[str, Any], keep_days: int = 45) -> Dict[str, Any]:
    # remove old fingerprints to keep state small
    cutoff = datetime.now(TZ_TAIPEI) - timedelta(days=keep_days)
    out = {}
    for fp, meta in (sent or {}).items():
        ts = None
        if isinstance(meta, dict):
            ts = meta.get("ts")
        elif isinstance(meta, str):
            ts = meta
        if not ts:
            out[fp] = meta
            continue
        try:
            dt = datetime.fromisoformat(ts)
        except Exception:
            out[fp] = meta
            continue
        if dt >= cutoff:
            out[fp] = meta
    return out

def mask(s: str) -> str:
    if not s:
        return "(empty)"
    return s[:4] + "..." + s[-4:] if len(s) >= 10 else "***"

def log_env(name: str, value: str) -> None:
    print(f"[ENV] {name} = {mask(value)}")
    
# =========================
# Main run
# =========================

def run() -> int:
    gh_token = os.getenv("GITHUB_TOKEN", "")
    gh_repo = os.getenv("GITHUB_REPOSITORY", "")
    issue_number = os.getenv("ISSUE_NUMBER", "")

    tg_token = os.getenv("TG_BOT_TOKEN", "")
    tg_chat = os.getenv("TG_CHAT_ID", "")
    wecom_webhook = os.getenv("WECOM_WEBHOOK", "")

    # ✅ 加在这里（就在 state = load_state() 之前）
    log_env("TG_BOT_TOKEN", tg_token)
    log_env("TG_CHAT_ID", tg_chat)
    log_env("WECOM_WEBHOOK", wecom_webhook)

    state = load_state()
    sent = state.get("sent", {})
    sent = prune_sent(sent, keep_days=45)

    collected: List[ShoeItem] = []

    # 1) calendars
    for src in CALENDAR_SITES:
        try:
            html = http_get(src["url"])
            items = parse_calendar_page(html, src["url"], src["name"])
            collected.extend(items)
        except Exception as e:
            print(f"[WARN] calendar fetch/parse failed: {src['name']} | {e}")

    # 2) news latest -> articles
    for src in NEWS_SOURCES:
        try:
            html = http_get(src["url"])
            links = extract_article_links(html, src["url"])[:MAX_ARTICLES_PER_SOURCE]
            for u in links:
                try:
                    art_html = http_get(u)
                    item = parse_article(art_html, u, src["name"])
                    if item:
                        collected.append(item)
                except Exception as e:
                    print(f"[WARN] article parse failed: {u} | {e}")
        except Exception as e:
            print(f"[WARN] news fetch failed: {src['name']} | {e}")

    # 3) dedupe within run
    uniq: Dict[str, ShoeItem] = {}
    for it in collected:
        fp = build_fingerprint(it)
        # keep the higher-priority one if collision
        if fp not in uniq:
            uniq[fp] = it
        else:
            if priority_rank(it.priority) > priority_rank(uniq[fp].priority):
                uniq[fp] = it
            elif it.priority_score > uniq[fp].priority_score:
                uniq[fp] = it

    items_sorted = sort_items(list(uniq.values()))

    # 4) push only "new"
    pushed = 0
    for it in items_sorted:
        if pushed >= MAX_PUSH_ITEMS_PER_RUN:
            break

        fp = build_fingerprint(it)
        if fp in sent:
            continue

        msg = format_push_message(it)

        # Telegram
        try:
            send_telegram(tg_token, tg_chat, msg)
        except Exception as e:
            print(f"[WARN] Telegram send failed: {e}")

        # WeCom
        try:
            send_wecom(wecom_webhook, msg)
        except Exception as e:
            print(f"[WARN] WeCom send failed: {e}")

        # GitHub Issue comment (triggers email notifications if you watch the repo/issue)
        try:
            post_github_issue_comment(gh_token, gh_repo, issue_number, msg)
        except Exception as e:
            print(f"[WARN] GitHub comment failed: {e}")

        sent[fp] = {"ts": now_taipei_iso()}
        pushed += 1

    state["sent"] = sent
    save_state(state)

    print(f"Pushed {pushed} items.")
    return 0

if __name__ == "__main__":
    raise SystemExit(run())
