import os
import re
import json
import time
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# =============================================================================
# ColdTreasure V2.0 — Sneaker Intelligence Engine
# - Calendar monitoring (SBD / SneakerNews)
# - News monitoring (SBD / SneakerNews)
# - Brand/model intelligence (100+ brands, 50+ models)
# - Collaboration detection (multi-collab supported)
# - Priority engine (Priority > BrandWeight > ReleaseDate)
# - Dedupe (URL + Shoe fingerprint)
# - Push: Telegram / WeCom / GitHub Issue comment (email via GitHub notifications)
# =============================================================================

# =========================
# Settings
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
    # SBD calendars
    {"name": "SBD | Sneaker Release Dates", "url": "https://sneakerbardetroit.com/sneaker-release-dates/"},
    {"name": "SBD | Air Jordan Release Dates", "url": "https://sneakerbardetroit.com/air-jordan-release-dates/"},
    # SneakerNews calendars
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

# 100+ brand dictionary (aliases)
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
    "ECCO": ["ECCO"],
    "Timberland": ["Timberland"],
    "The North Face": ["The North Face", "TNF"],
    "Arc'teryx": ["Arc'teryx", "Arcteryx"],
    "Columbia": ["Columbia"],
    "Nike SB": ["Nike SB", "SB"],  # signal only
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
    "Fear of God Athletics": ["Fear of God Athletics", "FOG Athletics"],
    "A-COLD-WALL*": ["A-COLD-WALL", "A-COLD-WALL*"],
    "Human Made": ["Human Made"],
    "WTAPS": ["WTAPS"],
    "NEIGHBORHOOD": ["NEIGHBORHOOD", "Neighborhood"],
    "Rhude": ["Rhude"],
    "UNDEFEATED": ["UNDEFEATED"],
    "Billionaire Boys Club": ["Billionaire Boys Club", "BBC"],
    "Patta": ["Patta"],
    "Noah": ["Noah"],
    "Aimé Leon Dore": ["Aimé Leon Dore", "Aime Leon Dore", "ALD"],
    "JJJJound": ["JJJJound"],
    "Stone Island": ["Stone Island"],
    "Comme des Garçons": ["Comme des Garçons", "Comme des Garcons", "CDG"],
    "BEAMS": ["BEAMS"],
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
    "Palm Angels": ["Palm Angels"],

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
    "Drake": ["Drake"],
    "Bad Bunny": ["Bad Bunny"],
    "Pharrell": ["Pharrell", "Pharrell Williams"],
    "J Balvin": ["J Balvin"],
    "Salehe Bembury": ["Salehe Bembury"],
    "KAWS": ["KAWS"],
    "Takashi Murakami": ["Takashi Murakami", "Murakami"],
    "G-Dragon": ["G-Dragon", "G Dragon", "GD", "PEACEMINUSONE"],
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
    "Palace": CAT_STREET, "Fear of God": CAT_STREET, "Fear of God Athletics": CAT_STREET, "A-COLD-WALL*": CAT_STREET,
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

# 50+ model signals -> main brand (highest priority)
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
    "Streakfly": "Nike",
    "ZoomX": "Nike",
    "Flyknit": "Nike",

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

    # Vans
    "Old Skool": "Vans",
    "Sk8-Hi": "Vans",
    "Authentic": "Vans",
    "Slip-On": "Vans",

    # Puma
    "Suede": "Puma",
    "Clyde": "Puma",
    "RS-X": "Puma",

    # Converse
    "Chuck 70": "Converse",
    "Chuck Taylor": "Converse",
    "All Star": "Converse",

    # HOKA
    "Clifton": "HOKA",
    "Bondi": "HOKA",
    "Speedgoat": "HOKA",

    # Saucony
    "Jazz": "Saucony",
    "Shadow": "Saucony",
}

# Luxury self-owned models special case
LUXURY_MODELS: Dict[str, str] = {
    "Triple S": "Balenciaga",
    "Cloudbust": "Prada",
    "LV Trainer": "Louis Vuitton",
}

# Brand weight (your rule: Priority > BrandWeight > ReleaseDate)
BRAND_WEIGHT: Dict[str, int] = {
    "Nike": 100, "Jordan": 98, "adidas": 95, "New Balance": 85, "ASICS": 75, "Salomon": 70,
    "HOKA": 62, "Puma": 60, "Vans": 58, "Converse": 55,
    "Li-Ning": 52, "ANTA": 48, "361°": 45, "Fila": 42,
    # luxury (still weighty for sorting inside same priority)
    "Louis Vuitton": 92, "Dior": 92, "Gucci": 90, "Balenciaga": 88, "Prada": 86,
}

LUXURY_OR_JEWELRY = {
    "Dior", "Louis Vuitton", "Gucci", "Balenciaga", "Prada",
    "Tiffany & Co.", "Swarovski", "Cartier", "Bvlgari", "Chrome Hearts"
}
TOP_STREET_CULTURE = {
    "Travis Scott", "Off-White", "Supreme", "Stüssy", "KITH", "CLOT", "Fragment",
    "A Ma Maniére", "Aimé Leon Dore", "Bad Bunny", "Pharrell", "J Balvin",
    "Salehe Bembury", "G-Dragon", "Fear of God", "Fear of God Athletics"
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

def priority_rank(p: str) -> int:
    # Higher is better
    return {"S": 4, "A": 3, "B": 2, "C": 1}.get(p, 0)

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
    # stable unique
    return sorted(set(found), key=lambda x: x.lower())

def detect_main_brand(title: str) -> str:
    t = (title or "").lower()

    # 1) Luxury self models special case
    for model, brand in LUXURY_MODELS.items():
        if model.lower() in t:
            return brand

    # 2) Model signals (shoe DNA)
    for model, brand in MODEL_BRAND_MAP.items():
        if model.lower() in t:
            if model.lower() == "yeezy":
                return "adidas"  # agreed
            return brand

    # 3) Sports-first fallback
    brands = detect_brands(title)
    sports_priority = [
        "Nike", "Jordan", "adidas", "New Balance", "ASICS", "Salomon",
        "HOKA", "Puma", "Vans", "Converse", "Li-Ning", "ANTA", "361°", "Fila"
    ]
    for b in sports_priority:
        if b in brands:
            return b

    # 4) If only luxury/street present
    if brands:
        return brands[0]

    return "Unknown"

def is_collaboration(title: str, brands: List[str]) -> bool:
    # rule confirmed:
    # - contains x/×/&/collaboration OR
    # - >= 2 brand words recognized
    t = (title or "").lower()
    if (" x " in t) or ("×" in t) or (" & " in t) or ("collaboration" in t) or (" collab" in t):
        return True
    return len(brands) >= 2

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
        m = re.search(r"(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?", txt)
        if not m:
            return None
        mm = int(m.group(1))
        dd = int(m.group(2))
        yy = m.group(3)
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
        mon = months.get(m.group(1).lower(), None)
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

    # release proximity (lowest importance)
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
    # Shoe-level dedupe fingerprint:
    # Prefer style_code; else normalized shoes_name + main brand
    name = (item.shoes_name or "").lower()
    name = re.sub(r"[^a-z0-9\s\-']", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    style = (item.style_code or "").strip().upper()
    key = f"{item.brand_main}|{style or name}"
    return sha256(key)

# =========================
# Parsing: Calendar
# =========================

STYLE_CODE_RE = re.compile(r"\b[A-Z0-9]{2,6}\d{2,6}[-–][A-Z0-9]{2,6}\b|\b[A-Z]{1,3}\d{3,6}[-–]\d{3}\b", re.I)
PRICE_RE = re.compile(r"\$\s?\d{2,5}(?:\.\d{2})?")
MONTH_WORDS = r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t|tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
DATE_RE = re.compile(rf"\b{MONTH_WORDS}\s+\d{{1,2}}(?:,\s*\d{{4}})?\b", re.I)

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

    blocks: List[Tuple[str, str]] = []  # (text, url)

    # anchor candidates
    for a in soup.select("a"):
        href = a.get("href") or ""
        text = safe_text(a.get_text(" "))
        if not text or len(text) < 6:
            continue

        if STYLE_CODE_RE.search(text) or any(b.lower() in text.lower() for b in [
            "nike", "jordan", "adidas", "new balance", "asics", "salomon", "puma", "vans", "hoka", "saucony"
        ]):
            abs_url = urljoin(base_url, href)
            blocks.append((text, abs_url))

    # broader blocks
    for el in soup.select("li, tr, article, .post, .entry-content p"):
        txt = safe_text(el.get_text(" "))
        if not txt or len(txt) < 30:
            continue
        if not (STYLE_CODE_RE.search(txt) or DATE_RE.search(txt) or PRICE_RE.search(txt)):
            continue
        blocks.append((txt, base_url))

    # de-dupe blocks
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

        # Your earlier rule: if no formal name, skip (approx)
        if (not shoes_name or shoes_name.lower() == "unknown") and (style_code == "Unknown"):
            continue

        main_brand = detect_main_brand(shoes_name)
        brands_all = detect_brands(shoes_name)

        collab: List[str] = []
        if is_collaboration(shoes_name, brands_all):
            collab = [b for b in brands_all if b != main_brand]

        # Yeezy special
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

    # Keep top reasonable number by score to avoid noise
    items.sort(
        key=lambda x: (priority_rank(x.priority), x.brand_weight, -9999 if x.release_days is None else -x.release_days),
        reverse=True
    )
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

        # SBD: typically /<slug>/ (and ends with /)
        if "sneakerbardetroit.com" in p.netloc:
            if p.path.count("/") >= 2 and p.path.endswith("/"):
                links.append(abs_url)

        # SneakerNews: /yyyy/mm/dd/slug/
        elif "sneakernews.com" in p.netloc:
            if re.search(r"/\d{4}/\d{2}/\d{2}/", p.path):
                links.append(abs_url)

    seen = set()
    out: List[str] = []
    for u in links:
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out[:50]

def parse_article(html: str, url: str, source_name: str) -> Optional[ShoeItem]:
    soup = BeautifulSoup(html, "html.parser")

    # Title
    title = ""
    h1 = soup.find("h1")
    if h1:
        title = safe_text(h1.get_text(" "))
    if not title:
        title = safe_text(soup.title.get_text(" ")) if soup.title else ""
    title = normalize_title(title) or "Unknown"

    # Body
    containers = []
    for sel in ["article", ".entry-content", ".post-content", ".single-post", ".post", "main"]:
        containers.extend(soup.select(sel))

    if containers:
        body = safe_text(" ".join(c.get_text(" ") for c in containers[:2]))
    else:
        body = safe_text(soup.get_text(" "))

    labeled = soup.get_text("\n")

    # Extract fields
    shoes_name = title
    style_code = "Unknown"
    release_date = "Unknown"
    price = "Unknown"

    m_sc = re.search(r"(Style\s*Code|SKU)\s*[:\-]\s*([A-Z0-9\-–]{6,20})", labeled, re.I)
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

    m_dt = re.search(
        r"(Release\s*Date|Dropping|Launch(?:es)?)\s*[:\-]\s*(" + MONTH_WORDS + r"\s+\d{1,2}(?:,\s*\d{4})?)",
        labeled,
        re.I
    )
    if m_dt:
        release_date = m_dt.group(2)
    else:
        md = DATE_RE.search(labeled)
        if md:
            release_date = md.group(0)

    # Main brand detection (title + early body)
    main_brand = detect_main_brand(shoes_name)
    brands_all = detect_brands(shoes_name + " " + body[:600])

    sneakerish = (main_brand != "Unknown") or (style_code != "Unknown") or (price != "Unknown") or (release_date != "Unknown")
    if not sneakerish:
        return None

    # Collaboration list (multi)
    collab: List[str] = []
    if is_collaboration(shoes_name, brands_all):
        collab = [b for b in brands_all if b != main_brand]

    # Special cases:
    # 1) Off-White own model: if title has Off-White and no strong Nike model signals -> main Off-White
    if "Off-White" in brands_all and main_brand in ("Nike", "Unknown"):
        nike_model_signals = ["air force", "af1", "dunk", "air max", "jordan", "kobe", "vomero", "pegasus", "blazer"]
        if not any(sig in shoes_name.lower() for sig in nike_model_signals):
            main_brand = "Off-White"
            collab = [b for b in brands_all if b != main_brand]

    # 2) Yeezy special
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
# Dedupe & Sorting
# =========================

def ensure_state_schema(state: Dict[str, Any]) -> Dict[str, Any]:
    state.setdefault("v", 2)
    state.setdefault("dedupe", {})
    d = state["dedupe"]
    d.setdefault("urls", {})
    d.setdefault("shoes", {})
    return state

def seen_url(state: Dict[str, Any], url: str) -> bool:
    return url in state["dedupe"]["urls"]

def mark_url(state: Dict[str, Any], url: str) -> None:
    state["dedupe"]["urls"][url] = now_taipei_iso()

def seen_shoe_fp(state: Dict[str, Any], fp: str) -> bool:
    return fp in state["dedupe"]["shoes"]

def mark_shoe_fp(state: Dict[str, Any], fp: str, item: ShoeItem) -> None:
    state["dedupe"]["shoes"][fp] = {
        "at": now_taipei_iso(),
        "name": item.shoes_name,
        "style_code": item.style_code,
        "brand_main": item.brand_main,
        "source_type": item.source_type,
    }

def sort_items(items: List[ShoeItem]) -> List[ShoeItem]:
    # Your confirmed ordering:
    # Priority (S>A>B>C) > BrandWeight (higher better) > ReleaseDate (nearer better)
    def key(x: ShoeItem):
        # release_days: smaller is nearer; treat None as far
        rd = x.release_days if x.release_days is not None else 99999
        return (priority_rank(x.priority), x.brand_weight, -rd)
    return sorted(items, key=key, reverse=True)

# =========================
# Rendering (Push Template)
# =========================

def render_item(item: ShoeItem) -> str:
    # Strict lines with labels, as you required
    # Keep "Shoes Name / Style Code / Release Date / Price" and each on new line.
    collab_txt = ", ".join(item.brand_collab) if item.brand_collab else "None"
    head = f"【{item.priority} · {'Collaboration' if item.brand_collab else 'General Release'}】"
    return (
        f"{head}\n\n"
        f"Shoes Name: {item.shoes_name or 'Unknown'}\n"
        f"Style Code: {item.style_code or 'Unknown'}\n"
        f"Release Date: {item.release_date or 'Unknown'}\n"
        f"Price: {item.price or 'Unknown'}\n\n"
        f"Brand_Main: {item.brand_main}\n"
        f"Brand_Collab: {collab_txt}\n"
        f"Source: {item.source_type} | {item.source}\n"
        f"URL: {item.url}\n"
    )

def render_batch(items: List[ShoeItem]) -> str:
    parts = [render_item(x) for x in items]
    # Telegram/WeCom limit considerations: keep message reasonable
    return "\n--------------------\n".join(parts)

# =========================
# Push: Telegram / WeCom / GitHub Issue Comment
# =========================

def send_telegram(text: str) -> None:
    token = os.getenv("TG_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TG_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    requests.post(url, json={"chat_id": chat_id, "text": text, "disable_web_page_preview": False}, timeout=REQUEST_TIMEOUT).raise_for_status()

def send_wecom(text: str) -> None:
    webhook = os.getenv("WECOM_WEBHOOK") or os.getenv("WECOM_WEBHOOK_URL")
    if not webhook:
        return
    payload = {"msgtype": "text", "text": {"content": text}}
    requests.post(webhook, json=payload, timeout=REQUEST_TIMEOUT).raise_for_status()

def send_github_issue_comment(text: str) -> None:
    token = os.getenv("GITHUB_TOKEN")
    repo = os.getenv("GITHUB_REPOSITORY")
    issue_number = os.getenv("ISSUE_NUMBER")
    if not token or not repo or not issue_number:
        return
    api = f"https://api.github.com/repos/{repo}/issues/{issue_number}/comments"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    requests.post(api, headers=headers, json={"body": text}, timeout=REQUEST_TIMEOUT).raise_for_status()

def push_all(text: str) -> None:
    # Channel toggles (default: on)
    enable_tg = os.getenv("ENABLE_TELEGRAM", "1") == "1"
    enable_wecom = os.getenv("ENABLE_WECOM", "1") == "1"
    enable_issue = os.getenv("ENABLE_GITHUB_ISSUE", "1") == "1"

    errors = []

    if enable_tg:
        try:
            send_telegram(text)
        except Exception as e:
            errors.append(f"Telegram error: {e}")

    if enable_wecom:
        try:
            send_wecom(text)
        except Exception as e:
            errors.append(f"WeCom error: {e}")

    if enable_issue:
        try:
            send_github_issue_comment(text)
        except Exception as e:
            errors.append(f"GitHub Issue error: {e}")

    if errors:
        print("\n".join(errors))

# =========================
# Collectors
# =========================

def collect_calendar_items() -> List[ShoeItem]:
    out: List[ShoeItem] = []
    for s in CALENDAR_SITES:
        try:
            html = http_get(s["url"])
            items = parse_calendar_page(html, s["url"], s["name"])
            # Keep top per source to avoid noisy pages
            out.extend(items[:MAX_PUSH_ITEMS_PER_RUN])
        except Exception as e:
            print(f"[calendar] fetch/parse failed: {s['name']} {s['url']} -> {e}")
    return out

def collect_news_items() -> List[ShoeItem]:
    out: List[ShoeItem] = []
    for src in NEWS_SOURCES:
        try:
            home_html = http_get(src["url"])
            links = extract_article_links(home_html, src["url"])
            # Only check first N links per source
            links = links[:MAX_ARTICLES_PER_SOURCE]
            for u in links:
                try:
                    html = http_get(u)
                    item = parse_article(html, u, src["name"])
                    if item:
                        out.append(item)
                except Exception as e2:
                    print(f"[news] article failed: {u} -> {e2}")
        except Exception as e:
            print(f"[news] source failed: {src['name']} {src['url']} -> {e}")
    return out

# =========================
# Main
# =========================

def main() -> None:
    state = ensure_state_schema(load_state())

    # 1) Collect candidates
    calendar_items = collect_calendar_items()
    news_items = collect_news_items()

    candidates = calendar_items + news_items
    if not candidates:
        print("No candidates found.")
        return

    # 2) Dedupe by URL + Shoe fingerprint
    new_items: List[ShoeItem] = []
    for item in candidates:
        if item.url and seen_url(state, item.url):
            continue

        fp = build_fingerprint(item)
        if seen_shoe_fp(state, fp):
            # Still mark URL, so we don't re-fetch/push this exact URL repeatedly
            if item.url:
                mark_url(state, item.url)
            continue

        # New item
        new_items.append(item)
        if item.url:
            mark_url(state, item.url)
        mark_shoe_fp(state, fp, item)

    if not new_items:
        print("No new items after dedupe.")
        save_state(state)
        return

    # 3) Sort per your rule: Priority > BrandWeight > ReleaseDate
    new_items = sort_items(new_items)

    # 4) Safety cap
    new_items = new_items[:MAX_PUSH_ITEMS_PER_RUN]

    # 5) Push
    message = render_batch(new_items)
    push_all(message)

    # 6) Persist state
    save_state(state)

    print(f"Pushed {len(new_items)} items.")

if __name__ == "__main__":
    main()
