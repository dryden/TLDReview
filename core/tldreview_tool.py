#!/usr/bin/env python3
"""TL;DReview - Google Maps Review Extraction & Curation Tool.

Official Website: https://tldreview.app/

A zero-dependency standalone tool designed for agent sandboxes, including
Meta Muse and Grok Bot cloud computers.
Supports:
1. Direct Google Maps URLs (including short links & redirect wrappers)
2. Natural-language / nearby search queries with optional user coordinates (e.g., "附近的全家便利商店")
3. Store signboard / menu photo inputs (via Agent Vision or built-in EXIF GPS + Vision OCR)

Extracts or searches the best-matching Google Maps place, grabs reviews via SerpApi,
filters and trims reviews for maximum density, and outputs structured data
for an LLM to analyze.

Zero third-party dependencies: uses only Python 3 standard library.
"""
from __future__ import annotations

import argparse
import base64
import concurrent.futures
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import math
import mimetypes
import os
import re
import secrets
import struct
import sys
import time
from typing import Any, Iterable, List, Optional, Tuple
import urllib.error
import urllib.parse
import urllib.request
import zlib

# ---------------------------------------------------------------------------
# Constants & Tunables (Aligned with production tldreview engine)
# ---------------------------------------------------------------------------
SERPAPI_ENDPOINT = "https://serpapi.com/search.json"
GEMINI_VISION_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
)
HTTP_TIMEOUT = 30.0
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Review selection and token conservation limits
RECENT_MAX_AGE = timedelta(days=365 * 2)  # 2 years
MIN_SELECTED_REVIEWS = 10
MIN_RECENT_LOCAL_GUIDES = 6
MIN_TOTAL_FOR_EN_REFETCH = 10
MAX_REVIEWS_FOR_LLM = 30
MAX_TOTAL_REVIEW_CHARS = 8000
MAX_SINGLE_REVIEW_CHARS = 1200
COMPARE_MAX_REVIEWS_PER_PLACE = 15
COMPARE_MAX_CHARS_PER_PLACE = 4000
NEXT_PAGE_THRESHOLD = 500
ELLIPSIS = "…"

# Regular Expressions for URL, Coordinate & ID Extraction
_URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_CID_RE = re.compile(r"0x[0-9a-f]+:0x[0-9a-f]+", re.IGNORECASE)
_PLACE_ID_RE = re.compile(r"ChI[0-9A-Za-z_-]+")
_HEX_ID_RE = re.compile(r"[0-9a-f]{16,}", re.IGNORECASE)
_COORD_BANG_RE = re.compile(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)")
_COORD_AT_RE = re.compile(r"@(-?\d+\.\d+),(-?\d+\.\d+)")
_LL_STR_RE = re.compile(
    r"^@?\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)(?:\s*,\s*(\d+(?:\.\d+)?)z?)?\s*$",
    re.IGNORECASE,
)
_INLINE_COORD_RE = re.compile(
    r"(?:@|(?<=\s)|^)(-?\d{1,2}\.\d{3,})\s*,\s*(-?\d{1,3}\.\d{3,})(?:,\s*\d+(?:\.\d+)?z?)?"
)
_PROVIDER_ID_RE = re.compile(r"/g/[0-9A-Za-z_-]+", re.IGNORECASE)

# Country code to official language mapping
COUNTRY_LANG = {
    "TW": "zh-tw", "HK": "zh-tw", "MO": "zh-tw", "CN": "zh-cn", "SG": "en",
    "JP": "ja", "KR": "ko", "TH": "th", "VN": "vi", "ID": "id", "MY": "ms",
    "PH": "en", "IN": "en", "US": "en", "GB": "en", "AU": "en", "NZ": "en",
    "CA": "en", "IE": "en", "FR": "fr", "BE": "fr", "DE": "de", "AT": "de",
    "CH": "de", "IT": "it", "ES": "es", "PT": "pt-pt", "BR": "pt-br",
    "MX": "es-419", "AR": "es-419", "CL": "es-419", "CO": "es-419", "PE": "es-419",
    "NL": "nl", "SE": "sv", "NO": "no", "DK": "da", "FI": "fi", "PL": "pl",
    "CZ": "cs", "HU": "hu", "GR": "el", "TR": "tr", "RU": "ru", "UA": "uk",
    "IL": "iw", "AE": "ar", "SA": "ar", "EG": "ar", "MA": "ar", "ZA": "en",
}

COUNTRY_NAMES = {
    "taiwan": "TW", "台灣": "TW", "臺灣": "TW", "hong kong": "HK", "香港": "HK",
    "macau": "MO", "澳門": "MO", "china": "CN", "中国": "CN", "中國": "CN",
    "japan": "JP", "日本": "JP", "south korea": "KR", "korea": "KR", "대한민국": "KR",
    "韓國": "KR", "thailand": "TH", "ประเทศไทย": "TH", "vietnam": "VN", "việt nam": "VN",
    "indonesia": "ID", "malaysia": "MY", "singapore": "SG", "philippines": "PH",
    "india": "IN", "united states": "US", "usa": "US", "united kingdom": "GB", "uk": "GB",
    "australia": "AU", "new zealand": "NZ", "canada": "CA", "ireland": "IE",
    "france": "FR", "germany": "DE", "deutschland": "DE", "italy": "IT", "italia": "IT",
    "spain": "ES", "españa": "ES", "portugal": "PT", "brazil": "BR", "mexico": "MX",
}


def _redact_api_key(text: str, api_key: Optional[str] = None) -> str:
    """Mask API keys in URLs or exception messages to prevent accidental leaks."""
    masked = re.sub(r"((?:api_)?key=)[^&\s\"']+", r"\1***", text, flags=re.IGNORECASE)
    if api_key and len(api_key) >= 4:
        masked = masked.replace(api_key, "***")
    return masked


SORT_BY_ALIASES = {
    "qualityscore": "qualityScore",
    "relevant": "qualityScore",
    "newest": "newestFirst",
    "newestfirst": "newestFirst",
    "lowest": "ratingLow",
    "lowest_rating": "ratingLow",
    "ratinglow": "ratingLow",
    "highest": "ratingHigh",
    "highest_rating": "ratingHigh",
    "ratinghigh": "ratingHigh",
}


def normalize_sort_by(sort_by: Optional[str] = None) -> str:
    """Normalize user-friendly sort aliases to SerpApi google_maps_reviews sort_by values."""
    if not sort_by:
        return "qualityScore"
    key = sort_by.strip().lower()
    if key not in SORT_BY_ALIASES:
        valid = ", ".join(sorted(set(SORT_BY_ALIASES.values())))
        raise ValueError(f"Unsupported sort_by '{sort_by}'. Valid options: {valid} (or newest/lowest/highest)")
    return SORT_BY_ALIASES[key]


def load_dotenv_if_present(dotenv_path: Optional[str] = None) -> None:
    """Zero-dependency loader for local .env files without overriding existing env vars."""
    candidates = [dotenv_path] if dotenv_path else [
        os.path.join(os.getcwd(), ".env"),
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env")),
    ]
    for path in candidates:
        if not path or not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                for raw_line in f:
                    line = raw_line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    if line.startswith("export "):
                        line = line[len("export "):].strip()
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("'\"")
                    if k and k not in os.environ:
                        os.environ[k] = v
        except OSError:
            pass


def resolve_api_key(explicit_key: Optional[str] = None) -> Optional[str]:
    """Resolve SERPAPI_API_KEY from explicit argument, environment, or local .env file."""
    if explicit_key:
        return explicit_key
    env_key = os.getenv("SERPAPI_API_KEY")
    if env_key:
        return env_key
    load_dotenv_if_present()
    return os.getenv("SERPAPI_API_KEY")


def log(msg: str) -> None:
    """Print progress logs to stderr to avoid polluting stdout."""
    print(f"[tldreview] {_redact_api_key(msg)}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------
@dataclass
class Review:
    author: str
    text: str
    rating: Optional[float] = None
    relative_time: Optional[str] = None
    published_at: Optional[str] = None  # ISO8601 UTC
    is_local_guide: Optional[bool] = None
    like_count: Optional[int] = None
    review_id: Optional[str] = None
    images: list[str] = field(default_factory=list)

    def published_dt(self) -> Optional[datetime]:
        if not self.published_at:
            return None
        try:
            dt = datetime.fromisoformat(self.published_at)
        except ValueError:
            return None
        # Normalize naive datetimes to UTC so comparisons with aware cutoffs don't raise
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    def dedup_key(self) -> Tuple[str, str]:
        """Return a stable deduplication key across pages and languages."""
        if self.review_id:
            return ("id", self.review_id)
        return (self.author, self.published_at or self.text)


@dataclass
class PlaceInfo:
    name: Optional[str] = None
    address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    types: list[str] = field(default_factory=list)
    rating_summary: dict[str, int] = field(default_factory=dict)
    average_rating: Optional[float] = None
    total_reviews: Optional[int] = None
    data_id: Optional[str] = None   # 0x...:0x... form returned by SerpApi
    place_id: Optional[str] = None  # ChI... form returned by SerpApi
    price: Optional[str] = None
    open_state: Optional[str] = None
    service_options: list[str] = field(default_factory=list)
    popular_times: dict[str, Any] = field(default_factory=dict)
    menu_urls: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Step 1: URL & ID Extraction (Ports of app/utils.py & handlers.py)
# ---------------------------------------------------------------------------
def _unwrap_consent_url(url: str) -> str:
    """Unwrap Google EU/regional consent redirect URLs (?continue=...)."""
    parsed = urllib.parse.urlparse(url)
    if parsed.netloc.lower().startswith("consent."):
        cont = urllib.parse.parse_qs(parsed.query).get("continue")
        if cont and cont[0]:
            return urllib.parse.unquote(cont[0]).strip()
    return url


def extract_google_maps_url(text: str) -> Optional[str]:
    """Extract a Google Maps URL from free-form text or link wrappers."""
    for match in _URL_RE.finditer(text):
        candidate = _unwrap_consent_url(match.group(0).rstrip(").,;'\"!?]>）！？，。、】》」』"))
        # Handle Instagram redirect wrapper: l.instagram.com/?u=...
        host = urllib.parse.urlparse(candidate).netloc.lower()
        if host.endswith("instagram.com"):
            query_params = urllib.parse.parse_qs(urllib.parse.urlparse(candidate).query)
            for target in query_params.get("u", []):
                decoded = _unwrap_consent_url(urllib.parse.unquote(target).strip())
                if is_google_maps_url(decoded):
                    return decoded

        if is_google_maps_url(candidate):
            return candidate
    return None


def is_google_maps_url(url: str) -> bool:
    """Check if URL belongs to Google Maps or short URL domain."""
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    netloc = parsed.netloc.lower()
    if netloc.startswith("maps.app.goo.gl") or netloc.startswith("goo.gl"):
        return True
    if netloc in {"share.google", "share.google.com"} or netloc.endswith(".share.google.com"):
        return True
    segments = netloc.split(".")
    if "google" not in segments:
        return False
    return parsed.path.startswith("/maps") or "maps" in parsed.path


def resolve_short_url(url: str, timeout: float = 10.0) -> str:
    """Follow HTTP redirect chains for short links and unwrap Google consent pages."""
    current_url = _unwrap_consent_url(url)
    for _ in range(2):
        parsed = urllib.parse.urlparse(current_url)
        host = parsed.netloc.lower()
        if not any(h in host for h in ("goo.gl", "share.google")):
            break

        log(f"Resolving short URL: {current_url}")
        try:
            req = urllib.request.Request(current_url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                resolved = resp.geturl() or current_url
        except urllib.error.HTTPError as exc:
            resolved = exc.geturl() or current_url
        except Exception as exc:
            log(f"URL resolve warning: {exc}")
            break

        next_url = _unwrap_consent_url(resolved)
        if next_url == current_url:
            break
        current_url = next_url

    return current_url


def extract_google_cid(url: str) -> Optional[str]:
    """Extract standard hex CID (0x...:0x...), prioritizing !1s and avoiding !5s."""
    decoded = urllib.parse.unquote(url)
    data_idx = decoded.find("data=")
    segment = decoded[data_idx:] if data_idx != -1 else decoded

    matches = list(_CID_RE.finditer(segment))
    if not matches:
        return None

    def prefix_of(m: re.Match) -> str:
        start = m.start()
        return segment[max(0, start - 3):start]

    for m in matches:
        if prefix_of(m).endswith("!1s"):
            return m.group(0)
    for m in matches:
        if not prefix_of(m).endswith("!5s"):
            return m.group(0)
    return matches[-1].group(0)


def extract_maps_data_id(url: str) -> Optional[str]:
    """Comprehensive extraction of Google Maps data_id matching production bot."""
    decoded = urllib.parse.unquote(url)
    data_idx = decoded.find("data=")
    segment = decoded[data_idx:] if data_idx != -1 else decoded

    # 1. Google 0x...:0x... CID (Primary for SerpApi)
    cid = extract_google_cid(decoded)
    if cid:
        return cid

    # 2. Hex data_id after !1s (without 0x prefix)
    for match in re.finditer(r"!1s([^!]+)", segment):
        cand = match.group(1)
        if cand and not cand.startswith("0x") and _HEX_ID_RE.fullmatch(cand):
            return cand.lower()

    # 3. Query parameters (cid / ftid)
    query_params = urllib.parse.parse_qs(urllib.parse.urlparse(decoded).query)
    for cand in query_params.get("cid", []):
        cand = cand.strip()
        if cand.isdigit() or _CID_RE.fullmatch(cand):
            return cand
    for cand in query_params.get("ftid", []):
        cand = cand.strip()
        if cand:
            return cand

    # 4. Google Place ID (ChI...)
    match = _PLACE_ID_RE.search(decoded)
    if match:
        return match.group(0)

    # 5. Fallback Provider ID (/g/...)
    p_match = _PROVIDER_ID_RE.search(decoded)
    if p_match:
        return p_match.group(0)

    return None


def parse_ll_string(ll: str) -> Optional[Tuple[float, float, Optional[int]]]:
    """Parse '@lat,lng,15z' or 'lat,lng' into (latitude, longitude, zoom)."""
    if not ll:
        return None
    m = _LL_STR_RE.match(ll.strip())
    if not m:
        return None
    try:
        lat = float(m.group(1))
        lng = float(m.group(2))
        zoom = int(float(m.group(3))) if m.group(3) is not None else None
    except ValueError:
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0):
        return None
    return lat, lng, zoom


def format_serpapi_ll(lat: float, lng: float, zoom: int = 15) -> str:
    """Format latitude, longitude, and zoom into SerpApi's '@lat,lng,zoomz' parameter."""
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0):
        raise ValueError(f"Invalid coordinates: lat={lat}, lng={lng}")
    clamped_zoom = max(3, min(21, int(zoom)))
    return f"@{lat:.6f},{lng:.6f},{clamped_zoom}z"


def haversine_distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> int:
    """Calculate great-circle distance in meters between two WGS84 coordinates."""
    r = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    return int(round(2.0 * r * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))))


def extract_coordinates(url: str) -> Optional[Tuple[float, float]]:
    """Extract latitude and longitude from URL pin, viewport, or query parameter."""
    decoded = urllib.parse.unquote(url)
    for rx in (_COORD_BANG_RE, _COORD_AT_RE):
        m = rx.search(decoded)
        if m:
            lat, lng = float(m.group(1)), float(m.group(2))
            if -90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0:
                return lat, lng
    try:
        query_params = urllib.parse.parse_qs(urllib.parse.urlparse(decoded).query)
        for key in ("q", "query", "ll", "center", "sll"):
            for val in query_params.get(key, []):
                parsed = parse_ll_string(val)
                if parsed:
                    return parsed[0], parsed[1]
    except ValueError:
        pass
    return None


def extract_inline_coordinates(text: str) -> Tuple[str, Optional[Tuple[float, float]]]:
    """Extract inline coordinates (e.g. '@25.0330,121.5654' or '25.0330, 121.5654') from free text."""
    if not text:
        return "", None
    parsed_whole = parse_ll_string(text)
    if parsed_whole:
        return "", (parsed_whole[0], parsed_whole[1])

    m = _INLINE_COORD_RE.search(text)
    if not m:
        return text, None
    try:
        lat, lng = float(m.group(1)), float(m.group(2))
    except ValueError:
        return text, None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0):
        return text, None

    cleaned = (text[: m.start()] + " " + text[m.end() :]).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned, (lat, lng)


def extract_search_query_from_maps_url(url: str) -> Optional[str]:
    """Extract a place name or search query from a Google Maps URL that lacks a direct data_id."""
    decoded = urllib.parse.unquote(url)
    try:
        parsed = urllib.parse.urlparse(decoded)
    except ValueError:
        return None

    # 1. Check /maps/search/<query> or /maps/place/<name>
    for pattern in (r"/maps/search/([^/?#]+)", r"/maps/place/([^/?#]+)"):
        m = re.search(pattern, parsed.path)
        if m:
            cand = m.group(1).replace("+", " ").strip()
            if cand and not cand.startswith("data=") and not parse_ll_string(cand):
                return cand

    # 2. Check ?q=<query> or ?query=<query>
    query_params = urllib.parse.parse_qs(parsed.query)
    for key in ("q", "query"):
        for val in query_params.get(key, []):
            cand = val.replace("+", " ").strip()
            if cand and not parse_ll_string(cand):
                return cand
    return None


_CONV_PREFIX_RE = re.compile(
    r"^(?:請問?|幫我|麻煩|請幫我|可以幫我)?(?:查詢|查一下|查查看|找一下|找看看|搜尋|看看|查|找)\s*",
    re.IGNORECASE,
)
_CONV_SUFFIX_RE = re.compile(
    r"\s*(?:這家店?)?(?:的?(?:Google\s*Maps?\s*)?(?:評價|評論|好不好吃?|值得去嗎|推薦嗎|避雷|報告|如何))[？?！!。.]*$",
    re.IGNORECASE,
)
_NEARBY_PREFIX_RE = re.compile(
    r"^(?:在?我?(?:目前|現在)?(?:所在)?(?:位置)?(?:這附近|我附近|附近|周邊|周遭|身邊)的?)\s*",
    re.IGNORECASE,
)
_NEARBY_EN_PREFIX_RE = re.compile(r"^(?:near\s+me|nearby|closest|nearest)\s+", re.IGNORECASE)
_NEARBY_SUFFIX_RE = re.compile(
    r"\s*(?:near\s+me|nearby|around\s+here|around\s+me|在這?附近)$",
    re.IGNORECASE,
)


def clean_search_query(raw_query: str, has_coords: bool = False) -> str:
    """Clean conversational filler and 'nearby' prefixes from a natural-language place query."""
    if not raw_query:
        return ""
    q = raw_query.strip().strip("'\"「」『』")
    q = _CONV_PREFIX_RE.sub("", q).strip()
    q = _CONV_SUFFIX_RE.sub("", q).strip()

    # Strip '附近的' / 'nearby' prefix or suffix so Google Maps searches the store/category cleanly
    stripped = _NEARBY_PREFIX_RE.sub("", q).strip()
    stripped = _NEARBY_EN_PREFIX_RE.sub("", stripped).strip()
    stripped = _NEARBY_SUFFIX_RE.sub("", stripped).strip()
    if stripped:
        q = stripped

    return q or raw_query.strip()


# ---------------------------------------------------------------------------
# Step 1B: Photo / Signboard / Menu Support (Zero-dependency EXIF GPS + Vision)
# ---------------------------------------------------------------------------
def _parse_tiff_gps(tiff: bytes) -> Optional[Tuple[float, float]]:
    """Extract WGS84 (lat, lng) from a raw TIFF header containing an EXIF GPSInfo IFD."""
    if len(tiff) < 8:
        return None
    byte_order = tiff[:2]
    if byte_order == b"II":
        endian = "<"
    elif byte_order == b"MM":
        endian = ">"
    else:
        return None

    magic, ifd0_offset = struct.unpack(endian + "HI", tiff[2:8])
    if magic != 42 or ifd0_offset + 2 > len(tiff):
        return None

    type_sizes = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8}

    def read_ifd(offset: int) -> dict[int, Tuple[int, int, bytes]]:
        if offset + 2 > len(tiff):
            return {}
        count = struct.unpack(endian + "H", tiff[offset : offset + 2])[0]
        entries: dict[int, Tuple[int, int, bytes]] = {}
        pos = offset + 2
        for _ in range(min(count, 256)):
            if pos + 12 > len(tiff):
                break
            tag, ftype, num = struct.unpack(endian + "HHI", tiff[pos : pos + 8])
            val_raw = tiff[pos + 8 : pos + 12]
            pos += 12
            tsize = type_sizes.get(ftype, 1) * num
            if tsize <= 4:
                data = val_raw[:tsize]
            else:
                data_off = struct.unpack(endian + "I", val_raw)[0]
                if data_off + tsize > len(tiff):
                    continue
                data = tiff[data_off : data_off + tsize]
            entries[tag] = (ftype, num, data)
        return entries

    ifd0 = read_ifd(ifd0_offset)
    if 0x8825 not in ifd0:
        return None
    _, _, gps_ptr_bytes = ifd0[0x8825]
    if len(gps_ptr_bytes) < 4:
        return None
    gps_offset = struct.unpack(endian + "I", gps_ptr_bytes[:4])[0]
    gps_ifd = read_ifd(gps_offset)

    if 2 not in gps_ifd or 4 not in gps_ifd:
        return None

    def parse_dms(data: bytes) -> Optional[float]:
        if len(data) < 24:
            return None
        nums = struct.unpack(endian + "6I", data[:24])
        if nums[1] == 0 or nums[3] == 0 or nums[5] == 0:
            return None
        deg = nums[0] / nums[1]
        mins = nums[2] / nums[3]
        secs = nums[4] / nums[5]
        return deg + (mins / 60.0) + (secs / 3600.0)

    lat = parse_dms(gps_ifd[2][2])
    lng = parse_dms(gps_ifd[4][2])
    if lat is None or lng is None:
        return None

    lat_ref = gps_ifd.get(1, (2, 1, b"N"))[2].decode("ascii", "ignore").strip("\x00").upper()
    lng_ref = gps_ifd.get(3, (2, 1, b"E"))[2].decode("ascii", "ignore").strip("\x00").upper()
    if lat_ref.startswith("S"):
        lat = -lat
    if lng_ref.startswith("W"):
        lng = -lng

    if not (-90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0):
        return None
    return round(lat, 6), round(lng, 6)


def extract_exif_gps(image_path: str) -> Optional[Tuple[float, float]]:
    """Extract EXIF GPS (latitude, longitude) from a JPEG/TIFF photo using only Python stdlib."""
    if not image_path or not os.path.isfile(image_path):
        return None
    try:
        with open(image_path, "rb") as f:
            head = f.read(262144)  # First 256 KB is sufficient for EXIF headers
    except OSError:
        return None

    try:
        if head.startswith((b"II*\x00", b"MM\x00*")):
            return _parse_tiff_gps(head)
        if not head.startswith(b"\xff\xd8"):
            return None
        idx = 2
        n = len(head)
        while idx + 4 <= n:
            if head[idx] != 0xFF:
                break
            marker = head[idx + 1]
            if marker == 0xDA or marker == 0xD9:  # SOS or EOI
                break
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                idx += 2
                continue
            seg_len = struct.unpack(">H", head[idx + 2 : idx + 4])[0]
            if seg_len < 2 or idx + 2 + seg_len > n:
                break
            if marker == 0xE1:  # APP1
                payload = head[idx + 4 : idx + 2 + seg_len]
                if payload.startswith(b"Exif\x00\x00"):
                    coords = _parse_tiff_gps(payload[6:])
                    if coords:
                        return coords
            idx += 2 + seg_len
    except Exception:
        return None
    return None


def extract_query_from_image(
    image_path: str,
    gemini_api_key: Optional[str] = None,
) -> dict[str, Any]:
    """Recognize store name, branch, and address from a signboard/menu photo via Gemini Vision API."""
    if not image_path or not os.path.isfile(image_path):
        raise ValueError(f"Image file not found: {image_path}")

    load_dotenv_if_present()
    key = gemini_api_key or os.getenv("GEMINI_API_KEY")
    if not key:
        raise ValueError(
            "To analyze an image directly via CLI (--image) without providing a store name query, "
            "set GEMINI_API_KEY in your environment/.env, OR let your AI Agent read the store name/"
            "address from the photo and pass it as the text query to tldreview_tool.py."
        )

    mime_type, _ = mimetypes.guess_type(image_path)
    if not mime_type or not mime_type.startswith("image/"):
        mime_type = "image/jpeg"

    with open(image_path, "rb") as f:
        b64_data = base64.b64encode(f.read()).decode("ascii")

    prompt = (
        "Analyze this photo (e.g. a store signboard, storefront, menu, receipt, or business card). "
        "Extract the store name, branch name (if any), address or road/district (if any), and phone number (if any). "
        "Return ONLY a valid JSON object with keys: "
        '{"store_name": "...", "branch_name": "...", "address": "...", "phone": "...", "search_query": "..."} '
        "where 'search_query' is the best concise Google Maps search query combining store_name, branch_name, and address."
    )
    body = json.dumps({
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": mime_type, "data": b64_data}},
            ]
        }],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.0},
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{GEMINI_VISION_ENDPOINT}?key={urllib.parse.quote(key)}",
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            resp_json = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        err_body = _redact_api_key(exc.read().decode("utf-8", "replace"), key)
        raise RuntimeError(f"Vision API HTTP {exc.code}: {err_body}") from None
    except Exception as exc:
        raise RuntimeError(f"Vision API request failed: {_redact_api_key(str(exc), key)}") from None

    candidates = resp_json.get("candidates") or []
    text_out = ""
    if candidates and isinstance(candidates[0], dict):
        parts = (candidates[0].get("content") or {}).get("parts") or []
        if parts and isinstance(parts[0], dict):
            text_out = str(parts[0].get("text") or "").strip()

    if not text_out:
        raise ValueError("Could not recognize store name or address from the provided image.")

    # Strip markdown code fences if present
    text_out = re.sub(r"^```(?:json)?\s*|\s*```$", "", text_out.strip(), flags=re.IGNORECASE)
    try:
        parsed = json.loads(text_out)
        if not isinstance(parsed, dict):
            parsed = {"search_query": str(parsed)}
    except ValueError:
        parsed = {"search_query": text_out.strip()}

    if not parsed.get("search_query"):
        parts_list = [
            str(parsed.get(k) or "").strip()
            for k in ("store_name", "branch_name", "address")
            if parsed.get(k)
        ]
        parsed["search_query"] = " ".join(parts_list).strip()

    if not parsed.get("search_query"):
        raise ValueError("No store name or address could be identified in the image.")

    return parsed


def infer_language(address: Optional[str]) -> str:
    """Infer place local language from address tokens, fallback to en."""
    if not address:
        return "en"
    for raw in reversed(re.split(r"[,，\n]+", address)):
        tok = re.sub(r"[\d\uFE0F]+", "", raw).strip(" ,，。・、").casefold()
        if not tok:
            continue
        if len(tok) == 2 and tok.isascii() and tok.upper() in COUNTRY_LANG:
            return COUNTRY_LANG[tok.upper()]
        for name, cc in COUNTRY_NAMES.items():
            if name.isascii():
                # Word-boundary match so e.g. "uk" doesn't match "fukuoka"
                matched = re.search(rf"\b{re.escape(name)}\b", tok) is not None
            else:
                matched = name in tok
            if matched:
                return COUNTRY_LANG.get(cc, "en")
    for name, cc in COUNTRY_NAMES.items():
        if not name.isascii() and name in address:
            return COUNTRY_LANG.get(cc, "en")
    return "en"


# ---------------------------------------------------------------------------
# Step 2: SerpApi Integration (Zero external dependencies)
# ---------------------------------------------------------------------------
_TRANSIENT_HTTP_CODES = {429, 500, 502, 503, 504}


def http_get_json(url: str, params: dict[str, Any], max_retries: int = 2) -> dict:
    """Perform HTTP GET request with transient retry and return JSON object."""
    api_key = str(params.get("api_key") or "")
    qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    full_url = f"{url}?{qs}"

    for attempt in range(max_retries + 1):
        req = urllib.request.Request(
            full_url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                data = resp.read().decode("utf-8", "replace")
                return json.loads(data)
        except urllib.error.HTTPError as exc:
            body = _redact_api_key(exc.read().decode("utf-8", "replace"), api_key)
            if exc.code in _TRANSIENT_HTTP_CODES and attempt < max_retries:
                delay = 0.5 * (2 ** attempt)
                log(f"SerpApi HTTP {exc.code} (transient), retrying in {delay:.1f}s...")
                time.sleep(delay)
                continue
            raise RuntimeError(f"SerpApi HTTP {exc.code}: {body}") from None
        except Exception as exc:
            safe_msg = _redact_api_key(str(exc), api_key)
            if attempt < max_retries:
                delay = 0.5 * (2 ** attempt)
                log(f"Network glitch ({safe_msg}), retrying in {delay:.1f}s...")
                time.sleep(delay)
                continue
            raise RuntimeError(f"Network request to SerpApi failed: {safe_msg}") from None

    raise RuntimeError("Network request to SerpApi exhausted retries")


def place_lookup_params(data_id: str) -> Optional[dict[str, str]]:
    """Map an extracted identifier to the SerpApi google_maps place-lookup parameter.

    SerpApi's `data_cid` expects a *decimal* CID. For a `0x<a>:0x<b>` data_id,
    the decimal CID is int(<b>, 16).
    """
    if _CID_RE.fullmatch(data_id):
        return {"data_cid": str(int(data_id.split(":")[1], 16))}
    if data_id.isdigit():
        return {"data_cid": data_id}
    if data_id.startswith("ChI"):
        return {"place_id": data_id}
    return None


def is_reviews_ready_id(data_id: str) -> bool:
    """True if the identifier is directly accepted by the google_maps_reviews engine."""
    return bool(_CID_RE.fullmatch(data_id) or data_id.startswith("ChI"))


def reviews_lookup_params(data_id: str) -> dict[str, str]:
    """Map an identifier to the google_maps_reviews parameter (place_id or data_id)."""
    if data_id.startswith("ChI"):
        return {"place_id": data_id}
    return {"data_id": data_id}


_SERVICE_KEY_LABELS: dict[str, str] = {
    "dine_in": "內用 (Dine-in)",
    "dine-in": "內用 (Dine-in)",
    "takeout": "外帶 (Takeout)",
    "takeaway": "外帶 (Takeout)",
    "delivery": "外送 (Delivery)",
    "no_contact_delivery": "無接觸外送 (No-contact delivery)",
    "curbside_pickup": "路邊取餐 (Curbside pickup)",
    "drive_through": "得來速 (Drive-through)",
    "outdoor_seating": "戶外座位 (Outdoor seating)",
    "in_store_pickup": "店內取貨 (In-store pickup)",
    "in_store_shopping": "店內購物 (In-store shopping)",
    "onsite_services": "現場服務 (Onsite services)",
    "reservable": "可訂位 (Reservable)",
    "wheelchair_accessible_entrance": "無障礙入口 (Wheelchair accessible)",
}

_DAY_ZH_MAP: dict[str, str] = {
    "monday": "週一",
    "tuesday": "週二",
    "wednesday": "週三",
    "thursday": "週四",
    "friday": "週五",
    "saturday": "週六",
    "sunday": "週日",
}

_MENU_TAG_RE = re.compile(
    r"(?:menu|菜單|菜单|價目|价目|價格表|价格表|メニュー|お品書き|메뉴|차림표|carte|speisekarte)",
    re.IGNORECASE,
)

_MENU_REVIEW_TEXT_RE = re.compile(
    r"(?:菜單|菜单|價目表|價目|價格表|價位|最新價格|漲價|調漲|\bmenu\b|price\s*list|メニュー|메뉴|가격표)",
    re.IGNORECASE,
)


def _extract_service_options(core: dict) -> list[str]:
    """Extract service options, accessibility, and key payment/planning tags from SerpApi place dict."""
    if not isinstance(core, dict):
        return []
    options: list[str] = []
    seen: set[str] = set()

    def _add(item: str) -> None:
        val = str(item or "").strip()
        if not val:
            return
        norm = val.casefold()
        if norm not in seen:
            seen.add(norm)
            options.append(val)

    raw_so = core.get("service_options")
    if isinstance(raw_so, dict):
        for k, v in raw_so.items():
            if v is True:
                k_clean = str(k).strip().lower().replace(" ", "_")
                label = _SERVICE_KEY_LABELS.get(k_clean, str(k).strip().replace("_", " "))
                _add(label)
            elif isinstance(v, str) and v.strip():
                _add(v.strip())
    elif isinstance(raw_so, list):
        for item in raw_so:
            if isinstance(item, str):
                _add(item)

    # Also check top-level boolean flags or extensions
    raw_ext = core.get("extensions")
    if isinstance(raw_ext, list):
        priority_ext_keys = {
            "service_options", "accessibility", "payments", "planning",
            "dining_options", "offerings", "amenities",
            "服務選項", "無障礙程度", "無障礙設施", "付款方式", "規劃", "用餐選擇",
        }
        for ext_block in raw_ext:
            if not isinstance(ext_block, dict):
                continue
            for k, vals in ext_block.items():
                k_norm = str(k).strip().lower()
                if k_norm in priority_ext_keys and isinstance(vals, list):
                    for v in vals:
                        if isinstance(v, str):
                            _add(v)

    return options[:8]


def _extract_popular_times(core: dict) -> dict[str, Any]:
    """Extract zero-cost popular_times, live busyness, and typical time spent from SerpApi place dict."""
    if not isinstance(core, dict):
        return {}
    pt = core.get("popular_times")
    result: dict[str, Any] = {}

    live_info: Optional[str] = None
    time_spent: Optional[str] = None

    if isinstance(pt, dict):
        live_hash = pt.get("live_hash")
        if isinstance(live_hash, dict):
            if isinstance(live_hash.get("info"), str) and live_hash["info"].strip():
                live_info = live_hash["info"].strip()
            if isinstance(live_hash.get("time_spent"), str) and live_hash["time_spent"].strip():
                time_spent = live_hash["time_spent"].strip()
        if not live_info and isinstance(pt.get("live_info"), str) and pt["live_info"].strip():
            live_info = pt["live_info"].strip()
        if not time_spent and isinstance(pt.get("typical_time_spent"), str) and pt["typical_time_spent"].strip():
            time_spent = pt["typical_time_spent"].strip()

        graph = pt.get("graph_results")
        if isinstance(graph, dict):
            peaks: list[dict[str, Any]] = []
            for day_key, slots in graph.items():
                if not isinstance(slots, list) or not slots:
                    continue
                day_norm = str(day_key).strip().lower()
                day_en = day_norm.capitalize()
                day_zh = _DAY_ZH_MAP.get(day_norm, day_en)

                high_slots: list[dict[str, Any]] = []
                max_slot: Optional[dict[str, Any]] = None
                for s in slots:
                    if not isinstance(s, dict):
                        continue
                    score = s.get("busyness_score")
                    try:
                        score_int = int(score) if score is not None else 0
                    except (ValueError, TypeError):
                        score_int = 0
                    if score_int <= 0:
                        continue
                    entry = {
                        "time": str(s.get("time") or "").strip(),
                        "score": score_int,
                        "info": str(s.get("info") or "").strip(),
                    }
                    if max_slot is None or score_int > max_slot["score"]:
                        max_slot = entry
                    if score_int >= 75:
                        high_slots.append(entry)

                if high_slots:
                    start_t = high_slots[0]["time"]
                    end_t = high_slots[-1]["time"]
                    window = f"{start_t}–{end_t}" if start_t != end_t else start_t
                    best_s = max(high_slots, key=lambda x: x["score"])
                    peaks.append({
                        "day": day_en,
                        "day_zh": day_zh,
                        "window": window,
                        "max_score": best_s["score"],
                        "info": best_s["info"],
                    })
                elif max_slot and max_slot["score"] >= 65:
                    peaks.append({
                        "day": day_en,
                        "day_zh": day_zh,
                        "window": max_slot["time"],
                        "max_score": max_slot["score"],
                        "info": max_slot["info"],
                    })

            if peaks:
                peaks.sort(key=lambda x: x["max_score"], reverse=True)
                result["peak_hours"] = peaks[:3]

    if not live_info and isinstance(core.get("live_busyness"), str) and core["live_busyness"].strip():
        live_info = core["live_busyness"].strip()
    if not time_spent and isinstance(core.get("typical_time_spent"), str) and core["typical_time_spent"].strip():
        time_spent = core["typical_time_spent"].strip()

    if live_info:
        result["live_info"] = live_info
    if time_spent:
        result["typical_time_spent"] = time_spent

    return result


def _extract_menu_urls(core: dict) -> list[str]:
    """Extract menu links or menu-tagged photo URLs from SerpApi place dict."""
    if not isinstance(core, dict):
        return []
    urls: list[str] = []
    seen: set[str] = set()

    def _add(u: Any) -> None:
        if not isinstance(u, str):
            return
        val = u.strip()
        if val.startswith(("http://", "https://")) and val not in seen:
            seen.add(val)
            urls.append(val)

    menu_field = core.get("menu")
    if isinstance(menu_field, str):
        _add(menu_field)
    elif isinstance(menu_field, dict):
        _add(menu_field.get("link") or menu_field.get("url") or menu_field.get("image"))

    for key in ("photos", "images", "categories"):
        items = core.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            label = " ".join(
                str(item.get(k) or "") for k in ("title", "category", "label", "tag", "name")
            )
            if _MENU_TAG_RE.search(label):
                _add(item.get("image") or item.get("link") or item.get("url") or item.get("thumbnail"))

    return urls[:2]


def _extract_menu_urls_from_reviews(reviews: list[Review], existing: Optional[list[str]] = None) -> list[str]:
    """Extract the newest 1-2 menu/price photo URLs from recent reviews mentioning menu or price keywords."""
    urls: list[str] = list(existing or [])
    seen: set[str] = set(urls)
    if len(urls) >= 2:
        return urls[:2]

    # Sort reviews with images by published_dt descending so newest menu photos come first
    with_images = [r for r in reviews if r.images]
    if not with_images:
        return urls

    epoch_min = datetime.min.replace(tzinfo=timezone.utc)
    with_images.sort(key=lambda r: r.published_dt() or epoch_min, reverse=True)

    for r in with_images:
        if _MENU_REVIEW_TEXT_RE.search(r.text):
            for img_url in r.images:
                if img_url not in seen:
                    seen.add(img_url)
                    urls.append(img_url)
                    if len(urls) >= 2:
                        return urls[:2]

    return urls[:2]


def _parse_place_dict(core: dict) -> PlaceInfo:
    """Parse a SerpApi place_results or local_results dictionary into PlaceInfo."""
    if not isinstance(core, dict):
        return PlaceInfo()

    name = core.get("title") or core.get("name")
    address = core.get("address") or core.get("formatted_address")

    # Coordinates
    coords = core.get("gps_coordinates") or core.get("gpsCoordinates") or {}
    lat = coords.get("latitude")
    lng = coords.get("longitude")

    # Types
    types = core.get("types") or core.get("type") or []
    if isinstance(types, str):
        types = [types]

    # Rating distribution
    rs_raw = core.get("rating_summary") or core.get("reviews_per_rating") or {}
    rating_summary: dict[str, int] = {}
    if isinstance(rs_raw, dict):
        for k, v in rs_raw.items():
            try:
                rating_summary[str(k)] = int(v)
            except (ValueError, TypeError):
                continue
    elif isinstance(rs_raw, list):
        for b in rs_raw:
            if isinstance(b, dict):
                try:
                    rating_summary[str(b.get("stars", ""))] = int(b.get("amount") or 0)
                except (ValueError, TypeError):
                    continue

    avg_rating = core.get("rating")
    try:
        avg_rating = float(avg_rating) if avg_rating is not None else None
    except (ValueError, TypeError):
        avg_rating = None

    total_reviews = core.get("reviews") or core.get("user_ratings_total")
    try:
        total_reviews = int(total_reviews) if total_reviews is not None else None
    except (ValueError, TypeError):
        total_reviews = None

    # Zero-cost metadata: price, open_state, service_options, popular_times, menu_urls
    raw_price = core.get("price") or core.get("price_level")
    price = str(raw_price).strip() if raw_price is not None and str(raw_price).strip() else None

    raw_open = core.get("open_state") or core.get("hours")
    open_state = str(raw_open).strip() if isinstance(raw_open, str) and raw_open.strip() else None

    service_options = _extract_service_options(core)
    popular_times = _extract_popular_times(core)
    menu_urls = _extract_menu_urls(core)

    return PlaceInfo(
        name=name,
        address=address,
        latitude=float(lat) if lat is not None else None,
        longitude=float(lng) if lng is not None else None,
        types=types,
        rating_summary=rating_summary,
        average_rating=avg_rating,
        total_reviews=total_reviews,
        data_id=core.get("data_id"),
        place_id=core.get("place_id"),
        price=price,
        open_state=open_state,
        service_options=service_options,
        popular_times=popular_times,
        menu_urls=menu_urls,
    )


def fetch_serpapi_place_details(api_key: str, data_id: str) -> PlaceInfo:
    """Fetch place details (name, address, rating distribution, types) via SerpApi."""
    lookup = place_lookup_params(data_id)
    if not lookup:
        log(f"Place details skipped: unsupported identifier format '{data_id}'")
        return PlaceInfo()
    params: dict[str, Any] = {
        "api_key": api_key,
        "engine": "google_maps",
        "output": "json",
        **lookup,
    }
    try:
        payload = http_get_json(SERPAPI_ENDPOINT, params)
    except Exception as exc:
        log(f"Place details request warning: {exc}")
        return PlaceInfo()

    core = payload.get("place_results") if isinstance(payload.get("place_results"), dict) else payload
    return _parse_place_dict(core if isinstance(core, dict) else {})


_CN_DIGITS = {
    "零": 0, "〇": 0,
    "一": 1, "壹": 1,
    "二": 2, "兩": 2, "貳": 2,
    "三": 3, "參": 3, "叁": 3,
    "四": 4, "肆": 4,
    "五": 5, "伍": 5,
    "六": 6, "陸": 6,
    "七": 7, "柒": 7,
    "八": 8, "捌": 8,
    "九": 9, "玖": 9,
}
_CN_UNITS = {
    "十": 10, "拾": 10,
    "百": 100, "佰": 100,
    "千": 1000, "仟": 1000,
}
_CN_NUM_SEQ_RE = re.compile(
    r"[零〇一二兩三四五六七八九十百千壹貳參叁肆伍陸柒捌玖拾佰仟]+"
)


def _cn_numerals_to_arabic(text: str) -> str:
    """Convert Chinese numeral sequences in store names (e.g. '五十嵐' -> '50嵐', '八十五度C' -> '85度C')."""
    if not text:
        return ""

    def _replace_seq(match: re.Match) -> str:
        seq = match.group(0)
        # If no positional unit (十/百/千), convert digit-by-digit (e.g. '八五' -> '85', '一零一' -> '101')
        if not any(ch in _CN_UNITS for ch in seq):
            return "".join(str(_CN_DIGITS[ch]) for ch in seq if ch in _CN_DIGITS)

        total = 0
        current = 0
        has_leading_unit = False
        for ch in seq:
            if ch in _CN_DIGITS:
                current = _CN_DIGITS[ch]
                has_leading_unit = True
            elif ch in _CN_UNITS:
                unit = _CN_UNITS[ch]
                if current == 0 and not has_leading_unit:
                    current = 1
                total += current * unit
                current = 0
                has_leading_unit = True
        total += current
        return str(total)

    return _CN_NUM_SEQ_RE.sub(_replace_seq, text)


def _normalize_for_match(text: str) -> str:
    """Normalize store names for matching (Chinese numerals, degree symbols, 7-ELEVEN aliases, etc.)."""
    s = text.casefold().replace("臺", "台")
    s = s.replace("℃", "度c").replace("°c", "度c").replace("ºc", "度c")
    s = re.sub(r"7[\s\-_]*eleven", "711", s)
    s = _cn_numerals_to_arabic(s)
    s = s.replace("小7", "711")
    s = re.sub(r"[\s\-_・店分號門市]+", "", s)
    return s


def _query_matches_title(query: str, title: Optional[str]) -> bool:
    """Check if the place title matches the core store name in the query."""
    if not query or not title:
        return False
    q_norm = _normalize_for_match(query)
    t_norm = _normalize_for_match(title)
    if not q_norm or not t_norm:
        return False
    if q_norm in t_norm or t_norm in q_norm:
        return True
    # Also check first token of query (e.g. "全家便利商店" -> "全家")
    first_tok = _normalize_for_match(re.split(r"\s+", query.strip())[0])
    for suffix in ("便利商店", "超商", "超市", "餐廳", "咖啡", "食堂", "茶飲", "專賣"):
        if first_tok.endswith(suffix) and len(first_tok) > len(suffix):
            short_brand = first_tok[: -len(suffix)]
            if len(short_brand) >= 2 and short_brand in t_norm:
                return True
    return False


def search_serpapi_place(
    api_key: str,
    query: str,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    zoom: int = 15,
    lang: str = "zh-tw",
) -> Tuple[str, PlaceInfo, list[dict[str, Any]]]:
    """Search Google Maps via SerpApi for a query (and optional coordinates) and return the best match."""
    has_coords = lat is not None and lng is not None
    cleaned_query = clean_search_query(query, has_coords=has_coords)
    if not cleaned_query:
        raise ValueError("Search query cannot be empty.")

    params: dict[str, Any] = {
        "api_key": api_key,
        "engine": "google_maps",
        "type": "search",
        "q": cleaned_query,
        "hl": (lang or "zh-tw").lower(),
        "output": "json",
    }
    if has_coords and lat is not None and lng is not None:
        params["ll"] = format_serpapi_ll(lat, lng, zoom)
        log(f"Searching Google Maps: q='{cleaned_query}' near ({lat:.5f}, {lng:.5f})")
    else:
        log(f"Searching Google Maps: q='{cleaned_query}'")

    payload = http_get_json(SERPAPI_ENDPOINT, params)

    # Case 1: Exact single place match (place_results)
    if isinstance(payload.get("place_results"), dict):
        p_info = _parse_place_dict(payload["place_results"])
        data_id = p_info.data_id or p_info.place_id
        if data_id:
            dist_m = None
            if has_coords and lat is not None and lng is not None and p_info.latitude is not None and p_info.longitude is not None:
                dist_m = haversine_distance_m(lat, lng, p_info.latitude, p_info.longitude)
            cand = {
                "name": p_info.name,
                "address": p_info.address,
                "rating": p_info.average_rating,
                "total_reviews": p_info.total_reviews,
                "data_id": data_id,
                "distance_m": dist_m,
            }
            return data_id, p_info, [cand]

    # Case 2: Multiple local search results (local_results)
    raw_locals = payload.get("local_results")
    if isinstance(raw_locals, list) and raw_locals:
        valid_entries = [
            e for e in raw_locals
            if isinstance(e, dict) and (e.get("data_id") or e.get("place_id"))
        ]
        if valid_entries:
            organic_entries = [e for e in valid_entries if not e.get("sponsored")]
            pool = organic_entries or valid_entries

            parsed_pool: list[Tuple[int, PlaceInfo, str, Optional[int]]] = []
            for idx, entry in enumerate(pool):
                p_info = _parse_place_dict(entry)
                cid = str(p_info.data_id or p_info.place_id)
                dist_m = None
                if (
                    has_coords
                    and lat is not None
                    and lng is not None
                    and p_info.latitude is not None
                    and p_info.longitude is not None
                ):
                    dist_m = haversine_distance_m(lat, lng, p_info.latitude, p_info.longitude)
                parsed_pool.append((idx, p_info, cid, dist_m))

            # Select the best match:
            # When user coordinates are provided and one or more results match the store/brand name
            # (e.g., searching "附近的全家便利商店" returns several FamilyMart branches), pick the
            # closest matching store by physical distance. Otherwise use Google's top relevance rank.
            chosen = parsed_pool[0]
            if has_coords:
                name_matched_with_dist = [
                    item for item in parsed_pool
                    if item[3] is not None and _query_matches_title(cleaned_query, item[1].name)
                ]
                if name_matched_with_dist:
                    name_matched_with_dist.sort(key=lambda item: (item[3] if item[3] is not None else 10**9, item[0]))
                    chosen = name_matched_with_dist[0]

            # Build top candidate list with chosen first
            ordered = [chosen] + [item for item in parsed_pool if item[2] != chosen[2]]
            candidates: list[dict[str, Any]] = []
            for _, p_item, cid_item, dist_item in ordered[:5]:
                candidates.append({
                    "name": p_item.name,
                    "address": p_item.address,
                    "rating": p_item.average_rating,
                    "total_reviews": p_item.total_reviews,
                    "data_id": cid_item,
                    "distance_m": dist_item,
                })

            return chosen[2], chosen[1], candidates

    raise ValueError(f"No matching Google Maps place found for query: '{cleaned_query}'")


def search_serpapi_top_places(
    api_key: str,
    query: str,
    top_n: int = 3,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    zoom: int = 15,
    lang: str = "zh-tw",
) -> Tuple[str, list[Tuple[str, PlaceInfo, Optional[int]]], int]:
    """Search Google Maps once, strip sponsored ads, and return top N highest-rated organic candidates."""
    has_coords = lat is not None and lng is not None
    cleaned_query = clean_search_query(query, has_coords=has_coords)
    if not cleaned_query:
        raise ValueError("Search query cannot be empty.")

    limit = max(1, min(int(top_n), 5))
    params: dict[str, Any] = {
        "api_key": api_key,
        "engine": "google_maps",
        "type": "search",
        "q": cleaned_query,
        "hl": (lang or "zh-tw").lower(),
        "output": "json",
    }
    if has_coords and lat is not None and lng is not None:
        params["ll"] = format_serpapi_ll(lat, lng, zoom)
        log(f"Area Discovery (Top-{limit}): q='{cleaned_query}' near ({lat:.5f}, {lng:.5f})")
    else:
        log(f"Area Discovery (Top-{limit}): q='{cleaned_query}'")

    payload = http_get_json(SERPAPI_ENDPOINT, params)

    if isinstance(payload.get("place_results"), dict):
        p_info = _parse_place_dict(payload["place_results"])
        data_id = p_info.data_id or p_info.place_id
        if data_id:
            dist_m = None
            if has_coords and lat is not None and lng is not None and p_info.latitude is not None and p_info.longitude is not None:
                dist_m = haversine_distance_m(lat, lng, p_info.latitude, p_info.longitude)
            return cleaned_query, [(str(data_id), p_info, dist_m)], 0

    raw_locals = payload.get("local_results")
    if isinstance(raw_locals, list) and raw_locals:
        valid_entries = [
            e for e in raw_locals
            if isinstance(e, dict) and (e.get("data_id") or e.get("place_id"))
        ]
        if valid_entries:
            sponsored_count = sum(1 for e in valid_entries if e.get("sponsored"))
            organic_entries = [e for e in valid_entries if not e.get("sponsored")]
            pool = organic_entries or valid_entries

            parsed_pool: list[Tuple[int, PlaceInfo, str, Optional[int]]] = []
            seen_ids: set[str] = set()
            for idx, entry in enumerate(pool):
                p_info = _parse_place_dict(entry)
                cid = str(p_info.data_id or p_info.place_id)
                if cid in seen_ids:
                    continue
                seen_ids.add(cid)
                dist_m = None
                if (
                    has_coords
                    and lat is not None
                    and lng is not None
                    and p_info.latitude is not None
                    and p_info.longitude is not None
                ):
                    dist_m = haversine_distance_m(lat, lng, p_info.latitude, p_info.longitude)
                parsed_pool.append((idx, p_info, cid, dist_m))

            # Prefer candidates with at least a minimal review base (>= 5) if enough exist
            credible_pool = [item for item in parsed_pool if (item[1].total_reviews or 0) >= 5]
            ranking_pool = credible_pool if len(credible_pool) >= limit else parsed_pool

            # Sort by highest average rating first, then review count, then original relevance index
            ranking_pool.sort(
                key=lambda item: (
                    -(item[1].average_rating or 0.0),
                    -(item[1].total_reviews or 0),
                    item[0],
                )
            )
            selected = [
                (cid, p_info, dist_m)
                for _, p_info, cid, dist_m in ranking_pool[:limit]
            ]
            return cleaned_query, selected, sponsored_count

    raise ValueError(f"No matching Google Maps places found for query: '{cleaned_query}'")


def parse_raw_reviews(payload: dict) -> list[Review]:
    """Extract and parse reviews from SerpApi reviews payload."""
    raw_list = payload.get("reviews") or payload.get("reviews_results") or []
    reviews: list[Review] = []

    for entry in raw_list:
        if not isinstance(entry, dict):
            continue
        # Extract review body
        text = entry.get("snippet") or entry.get("text") or entry.get("description")
        if not text or not str(text).strip():
            continue

        user = entry.get("user") if isinstance(entry.get("user"), dict) else {}
        author = user.get("name") or entry.get("author") or "Anonymous"
        raw_id = entry.get("review_id") or entry.get("id")
        review_id = str(raw_id).strip() if raw_id else None

        # Rating
        rating = None
        try:
            if entry.get("rating") is not None:
                rating = float(entry["rating"])
        except (ValueError, TypeError):
            pass

        # Relative time & published date
        relative_time = entry.get("date") or entry.get("relative_time")
        iso_date = entry.get("iso_date") or entry.get("isoDate")
        published_at = None
        if iso_date:
            try:
                clean_iso = str(iso_date).replace("Z", "+00:00")
                published_at = datetime.fromisoformat(clean_iso).isoformat()
            except ValueError:
                pass

        # Local guide & likes
        is_local_guide = bool(user.get("local_guide") or entry.get("is_local_guide"))
        likes = entry.get("likes") or entry.get("likesCount") or 0
        try:
            likes = int(likes)
        except (ValueError, TypeError):
            likes = 0

        # Review attached photos / images
        review_images: list[str] = []
        raw_imgs = entry.get("images") or entry.get("photos") or []
        if isinstance(raw_imgs, list):
            for img in raw_imgs:
                img_url = None
                if isinstance(img, str):
                    img_url = img.strip()
                elif isinstance(img, dict):
                    u = img.get("link") or img.get("image") or img.get("url") or img.get("thumbnail")
                    if isinstance(u, str):
                        img_url = u.strip()
                if img_url and img_url.startswith(("http://", "https://")) and img_url not in review_images:
                    review_images.append(img_url)

        reviews.append(
            Review(
                author=str(author).strip(),
                text=str(text).strip(),
                rating=rating,
                relative_time=str(relative_time).strip() if relative_time else None,
                published_at=published_at,
                is_local_guide=is_local_guide,
                like_count=likes,
                review_id=review_id,
                images=review_images,
            )
        )
    return reviews


def fetch_serpapi_reviews(
    api_key: str,
    data_id: str,
    lang: str = "en",
    page_token: Optional[str] = None,
    sort_by: str = "qualityScore",
) -> Tuple[list[Review], Optional[int], Optional[str], dict]:
    """Fetch reviews from SerpApi google_maps_reviews engine."""
    canonical_sort = normalize_sort_by(sort_by)
    lookup = reviews_lookup_params(data_id)
    params: dict[str, Any] = {
        "api_key": api_key,
        "engine": "google_maps_reviews",
        "hl": lang,
        "sort_by": canonical_sort,
        **lookup,
    }
    if page_token:
        params["next_page_token"] = page_token

    payload = http_get_json(SERPAPI_ENDPOINT, params)

    reviews = parse_raw_reviews(payload)

    # Total review count
    total = None
    place_info = payload.get("place_info") or {}
    for f in ("reviews", "review_count", "user_ratings_total"):
        if place_info.get(f) is not None:
            try:
                total = int(place_info[f])
                break
            except (ValueError, TypeError):
                continue

    # Next page token
    pagination = payload.get("serpapi_pagination") or {}
    next_token = pagination.get("next_page_token")

    return reviews, total, next_token, payload


# ---------------------------------------------------------------------------
# Step 3: Selection, Curation & Token Optimization
# ---------------------------------------------------------------------------
def _is_recent(r: Review, cutoff: datetime) -> bool:
    """Return True if review is within cutoff window or has no parseable date."""
    dt = r.published_dt()
    return dt is None or dt >= cutoff


def curate_reviews(reviews: list[Review]) -> list[Review]:
    """Curate reviews by prioritizing recent Local Guides, critical low ratings, high likes, and pad to 10."""
    if not reviews:
        return []

    cutoff = datetime.now(timezone.utc) - RECENT_MAX_AGE
    guides: list[Review] = []
    critical: list[Review] = []
    liked: list[Review] = []
    others: list[Review] = []

    for r in reviews:
        # Keep if within 2 years or if date is missing (can't determine age)
        if not _is_recent(r, cutoff):
            continue

        if r.is_local_guide:
            guides.append(r)
        elif r.rating is not None and r.rating <= 3.0:
            critical.append(r)
        elif (r.like_count or 0) > 1:
            liked.append(r)
        else:
            others.append(r)

    selected = guides + critical + liked + others

    # If < 10, pad with remaining reviews to ensure sufficient evidence
    if len(selected) < MIN_SELECTED_REVIEWS:
        selected_ids = {id(r) for r in selected}
        for r in reviews:
            if id(r) not in selected_ids:
                selected.append(r)
                if len(selected) >= MIN_SELECTED_REVIEWS:
                    break

    return selected or reviews


def _smart_truncate(text: str, limit: int) -> str:
    """Truncate text up to limit, preferring a natural sentence boundary near the end."""
    if len(text) <= limit:
        return text
    chunk = text[:limit]
    min_boundary = int(limit * 0.8)
    for idx in range(len(chunk) - 1, min_boundary - 1, -1):
        if chunk[idx] in "。！？.!?\n":
            return chunk[: idx + 1].rstrip()
    return chunk.rstrip()


def clip_reviews_for_prompt(
    reviews: list[Review],
    max_reviews: int = MAX_REVIEWS_FOR_LLM,
    max_total_chars: int = MAX_TOTAL_REVIEW_CHARS,
) -> list[Tuple[Review, str]]:
    """Strictly clip reviews to prevent prompt token bloat (supports custom budgets for compare/top-N)."""
    clipped: list[Tuple[Review, str]] = []
    total_chars = 0

    for r in reviews:
        if len(clipped) >= max_reviews or total_chars >= max_total_chars:
            break

        text = r.text.strip()
        cut = False

        # Single review length limit
        if len(text) > MAX_SINGLE_REVIEW_CHARS:
            text = _smart_truncate(text, MAX_SINGLE_REVIEW_CHARS)
            cut = True

        # Total review budget limit
        remaining = max_total_chars - total_chars
        if len(text) > remaining:
            if remaining <= 10:
                break
            text = _smart_truncate(text, remaining - 1)
            cut = True

        if not text:
            continue

        if cut:
            text += ELLIPSIS

        total_chars += len(text)
        clipped.append((r, text))

    return clipped


# ---------------------------------------------------------------------------
# Step 4: Output Assembly
# ---------------------------------------------------------------------------
def format_reviews_block(clipped: list[Tuple[Review, str]]) -> str:
    """Format reviews into high-density numbered lines for the LLM."""
    lines: list[str] = []
    for idx, (r, text) in enumerate(clipped, 1):
        rating_str = f" ★{r.rating:.0f}" if r.rating is not None else ""
        date_str = f" ({r.relative_time})" if r.relative_time else ""
        guide_str = " [Local Guide]" if r.is_local_guide else ""
        likes_str = f" [+{r.like_count} helpful]" if (r.like_count or 0) > 0 else ""
        lines.append(f"[{idx}] {r.author}{guide_str}{rating_str}{date_str}{likes_str}: {text}")
    return "\n".join(lines)


def _is_cross_language(fetch_lang: Optional[str], preferred_lang: Optional[str]) -> bool:
    """Return True when the primary fetched review language differs from the user's output language."""
    if not fetch_lang or not preferred_lang:
        return False
    primary_fetch = str(fetch_lang).split("+")[0].strip().lower()
    fetch_base = primary_fetch.split("-")[0]
    pref_base = str(preferred_lang).strip().lower().split("-")[0]
    return bool(fetch_base and pref_base and fetch_base != pref_base)


_PROMO_MANIPULATION_RE = re.compile(
    r"(?:五星好評|五顆星送|好評送|打卡送|評論送|給五星|五星送|集讚送|秀評論|出示評論|"
    r"5[\s\-]*star\s+review|review\s+for\s+(?:a\s+)?free|free\s+\w+\s+for\s+(?:a\s+)?review|"
    r"check[\s\-]*in\s+for\s+free|리뷰\s*이벤트|영수증\s*리뷰|口コミ投稿で|レビュー特典)",
    re.IGNORECASE,
)


def _audit_review_manipulation(
    reviews: list[dict[str, Any]],
    rating_summary: Optional[dict[str, int]] = None,
    average_rating: Optional[float] = None,
) -> dict[str, Any]:
    """Analyze curated reviews and rating distribution for fake-review / promo-incentive signals."""
    promo_mentions = 0
    for r in reviews:
        txt = str(r.get("text") or "")
        if _PROMO_MANIPULATION_RE.search(txt):
            promo_mentions += 1

    rs = rating_summary or {}
    total_rs = sum(int(v) for v in rs.values() if isinstance(v, (int, float)))
    one_star_ratio = 0.0
    five_star_ratio = 0.0
    if total_rs >= 10:
        one_star_ratio = int(rs.get("1", 0)) / total_rs
        five_star_ratio = int(rs.get("5", 0)) / total_rs

    bimodal_warning = bool(total_rs >= 20 and one_star_ratio >= 0.10 and five_star_ratio >= 0.55)
    if promo_mentions >= 2 or (promo_mentions >= 1 and bimodal_warning):
        suspicion = "high"
    elif promo_mentions == 1 or bimodal_warning:
        suspicion = "moderate"
    else:
        suspicion = "low"

    base_rating = float(average_rating or 0.0)
    penalty = (0.45 * min(promo_mentions, 3)) + (0.35 if bimodal_warning else 0.0)
    organic_score = round(max(0.0, base_rating - penalty), 2)

    return {
        "promo_mentions": promo_mentions,
        "bimodal_warning": bimodal_warning,
        "one_star_ratio": round(one_star_ratio, 3),
        "suspicion_level": suspicion,
        "organic_score": organic_score,
    }


def _format_popular_times_line(pt: dict[str, Any], is_zh: bool) -> Optional[str]:
    """Format popular_times dict into a concise single-line digest summary."""
    if not isinstance(pt, dict) or not pt:
        return None
    parts: list[str] = []
    live_info = pt.get("live_info")
    if live_info:
        parts.append(f"{'即時狀態' if is_zh else 'Live'}: {live_info}")

    peaks = pt.get("peak_hours")
    if isinstance(peaks, list) and peaks:
        peak_strs: list[str] = []
        for pk in peaks[:2]:
            if not isinstance(pk, dict):
                continue
            day_label = pk.get("day_zh") if is_zh else pk.get("day")
            win = pk.get("window") or ""
            score = pk.get("max_score")
            score_tag = f" ({score}%)" if score else ""
            if day_label and win:
                peak_strs.append(f"{day_label} {win}{score_tag}")
        if peak_strs:
            parts.append(f"{'尖峰時段' if is_zh else 'Peak Hours'}: {', '.join(peak_strs)}")

    time_spent = pt.get("typical_time_spent")
    if time_spent:
        parts.append(f"{'平均停留' if is_zh else 'Typical Stay'}: {time_spent}")

    if not parts:
        return None
    prefix = "⏳ 排隊與尖峰預警" if is_zh else "⏳ Popular Times & Wait Time"
    return f"{prefix}: {' | '.join(parts)}"


def format_markdown_output(result: dict, include_agent_instructions: bool = False) -> str:
    """Format pipeline result into a structured Markdown digest for CLI, MCP, and LangChain."""
    if result.get("mode") in ("compare", "top_discovery"):
        return format_comparison_markdown_output(result, include_agent_instructions=include_agent_instructions)
    if result.get("mode") in ("vault_save", "vault_query", "vault_delete", "vault_sync"):
        return format_vault_markdown_output(result)

    p = result.get("place") or {}
    s = result.get("stats") or {}
    search_meta = result.get("search") or {}
    target_lang = str(s.get("preferred_summary_language") or "en")
    is_zh = target_lang.lower().startswith("zh")
    fetch_lang = str(s.get("fetch_language") or "en")
    cross_lang = bool(s.get("cross_language")) or _is_cross_language(fetch_lang, target_lang)

    avg_rating = p.get("average_rating")
    if is_zh:
        rating_str = f"⭐ {avg_rating:.1f}" if avg_rating is not None else "⭐ 暫無評分"
    else:
        rating_str = f"⭐ {avg_rating:.1f}" if avg_rating is not None else "⭐ N/A"

    lines = [f"# {p.get('name') or 'Google Maps Place'}"]
    if search_meta:
        q_display = search_meta.get("cleaned_query") or search_meta.get("query") or ""
        dist_m = search_meta.get("matched_distance_m")
        lat_v = search_meta.get("latitude")
        lng_v = search_meta.get("longitude")
        if is_zh:
            loc_str = ""
            if dist_m is not None:
                loc_str = f" | 距離座標約 {dist_m} 公尺"
            elif lat_v is not None and lng_v is not None:
                loc_str = f" | 搜尋座標: {lat_v:.4f}, {lng_v:.4f}"
            lines.append(f"🔍 搜尋匹配: 「{q_display}」最佳符合店家{loc_str}")
        else:
            loc_str = ""
            if dist_m is not None:
                loc_str = f" | ~{dist_m}m from coordinates"
            elif lat_v is not None and lng_v is not None:
                loc_str = f" | Search coords: {lat_v:.4f}, {lng_v:.4f}"
            lines.append(f"🔍 Search Match: Best match for \"{q_display}\"{loc_str}")

        cands = search_meta.get("candidates") or []
        if len(cands) > 1:
            alt_parts: list[str] = []
            for idx, c in enumerate(cands[1:4], 2):
                c_name = c.get("name") or "Unknown"
                c_rating = f"⭐{c['rating']:.1f}" if c.get("rating") is not None else ""
                c_dist = f"{c['distance_m']}m" if c.get("distance_m") is not None else ""
                meta_items = [x for x in (c_rating, c_dist) if x]
                meta_str = f" ({', '.join(meta_items)})" if meta_items else ""
                alt_parts.append(f"{idx}. {c_name}{meta_str}")
            if alt_parts:
                alt_label = "📌 其他相近候選" if is_zh else "📌 Other Candidates"
                lines.append(f"{alt_label}: {' | '.join(alt_parts)}")

    if p.get("address"):
        lines.append(f"📍 {'地址' if is_zh else 'Address'}: {p['address']}")

    price_val = p.get("price")
    open_state_val = p.get("open_state")
    if is_zh:
        cat_line = f"🏷️ 分類: {p.get('type') or 'unknown'} | {rating_str} (總評數: {p.get('total_reviews') or 0})"
        if price_val:
            cat_line += f" | 💰 均消/價位: {price_val}"
        if open_state_val:
            cat_line += f" | 🕒 營業狀態: {open_state_val}"
        lines.append(cat_line)
    else:
        cat_line = f"🏷️ Category: {p.get('type') or 'unknown'} | {rating_str} (Total reviews: {p.get('total_reviews') or 0})"
        if price_val:
            cat_line += f" | 💰 Price: {price_val}"
        if open_state_val:
            cat_line += f" | 🕒 Status: {open_state_val}"
        lines.append(cat_line)

    service_opts = p.get("service_options") or []
    if service_opts:
        so_label = "🛎️ 服務與設施" if is_zh else "🛎️ Service & Options"
        lines.append(f"{so_label}: {' | '.join(str(x) for x in service_opts)}")

    pt_line = _format_popular_times_line(p.get("popular_times") or {}, is_zh=is_zh)
    if pt_line:
        lines.append(pt_line)

    menu_urls = p.get("menu_urls") or []
    if menu_urls:
        menu_label = "📋 最新菜單與價目圖片" if is_zh else "📋 Latest Menu & Price Photos"
        lines.append(f"{menu_label}: {' | '.join(str(u) for u in menu_urls)}")

    rs = p.get("rating_summary") or {}
    if rs:
        dist_parts = [f"{star}★({rs[str(star)]})" for star in (5, 4, 3, 2, 1) if str(star) in rs]
        if dist_parts:
            label = "星級分布" if is_zh else "Rating Distribution"
            lines.append(f"📈 {label}: {' | '.join(dist_parts)}")

    sort_label = s.get("sort_by") or "qualityScore"
    if is_zh:
        cross_note = " | 🗣️ 跨語系點餐手指卡已啟用" if cross_lang else ""
        lines.append(
            f"📊 評論抽樣: 採集 {s.get('used_reviews') or 0} 則關鍵評論 "
            f"(來源語系: {fetch_lang} | 排序: {sort_label}{cross_note})\n"
        )
        lines.append("## 精選評論清單 (Evidence Data)")
    else:
        cross_note = " | 🗣️ Point-and-Speak Bilingual Mode Active" if cross_lang else ""
        lines.append(
            f"📊 Review Sample: {s.get('used_reviews') or 0} curated reviews "
            f"(Source language: {fetch_lang} | Sort: {sort_label}{cross_note})\n"
        )
        lines.append("## Curated Reviews (Evidence Data)")
    lines.append(result.get("formatted_reviews") or "")

    if include_agent_instructions:
        if is_zh:
            lines.extend([
                "",
                "---",
                f"## 🦊 TL;DReview 分析與排版規範 (請以 `{target_lang}` 輸出)",
                "請嚴格根據上方精選評論證據（Evidence Data）與結構化元數據產出報告：",
                "1. **嚴禁幻覺（Anti-Hallucination）**：所有優缺點、招牌菜色、消費規則必須 100% 來自上述評論或官方元數據，未提及的維度請直接整段省略。",
                "2. **評分操縱示警**：若評論提及「五星好評送飲料/小菜/折價」等刷評活動，或星級呈極端 M 型分布，請務必於「💡 營運洞察與消費規則」明確示警。",
                "3. **兩極評價標註**：若同一項目評價分歧，請標註「（評價兩極）」並平實呈現雙方觀點。",
                "4. **排隊與尖峰預警**：若上方提供 `⏳ 排隊與尖峰預警`（即時擁擠度、尖峰時段、平均停留時間），請整合進「🤫 內行人的秘密撇步」提醒避開尖峰。",
                "5. **營業狀態與最新價格防呆**：請於開頭 Digest Header 保留 `💰 均消/價位`、`🕒 營業狀態` 與 `🛎️ 服務與設施`；若附有 `📋 最新菜單與價目圖片` 連結，請列出或以最新評論/菜單價格為準，避免舊評論的過時價格幻覺。",
                "6. **🗣️ 外國旅遊「點餐手指卡 / 生存小抄」**：當來源評論語系與輸出語言不同（如日本/韓國/泰國/法國等跨語言查詢）時，「🍜 必點招牌 / 核心亮點」中的每一道推薦品項**必須保留當地語言原始菜名對照**（例如：`炙燒和牛握壽司 (炙り和牛握り)` 或 `Braised Pork Belly (豚の角煮)`），方便旅客直接把手機螢幕指給店員看完成點餐。",
                "7. **7 大標準維度**：🍜 必點招牌 / 核心亮點、👍 優勢與推薦點、👎 缺點與避雷提醒、💡 營運洞察與消費規則、🤫 內行人的秘密撇步、🚗 交通與停車指南、🚨 特別點名。",
                "8. **🔒 結尾個人美食金庫提示（Post-Digest Nudge）**：於報告最後加上一行溫馨提示：「💡 *想把這間店加入『想去清單 (wishlist)』或標記為『喜歡 (liked) / 避雷 (disliked)』嗎？隨時告訴我即可為您加密儲存！*」",
            ])
        else:
            lines.extend([
                "",
                "---",
                f"## 🦊 TL;DReview Analysis & Formatting Guidelines (Output in `{target_lang}`)",
                "Synthesize a structured intelligence briefing strictly grounded in the Evidence Data and metadata above:",
                "1. **Anti-Hallucination**: Every dish, pro, con, or rule MUST be 100% backed by the reviews or metadata above. Omit any section that has no direct evidence.",
                "2. **Rating Manipulation Radar**: If reviews mention '5-star review/check-in for freebies/discounts' or show an extreme bimodal rating distribution, explicitly warn the user under '💡 Operational Insights & Rules'.",
                "3. **Conflicting Feedback**: When opinions diverge sharply on an item, label it `(Mixed feedback)` and present both sides objectively.",
                "4. **Popular Times & Wait Time**: If `⏳ Popular Times & Wait Time` is present above, integrate live busyness, peak hours, and typical stay duration into '🤫 Insider Tips'.",
                "5. **Open State, Price & Latest Menu Guardrails**: Include `💰 Price`, `🕒 Status`, and `🛎️ Service & Options` in the Digest Header. If `📋 Latest Menu & Price Photos` URLs are present, surface them and prioritize recent prices over outdated review prices.",
                "6. **🗣️ Traveler's Point-and-Speak Card**: Whenever the review source language differs from the output language (`cross_language`), every recommended dish in '🍜 Must-Try / Core Highlights' **MUST include the original local-language dish name in parentheses** (e.g., `Braised Pork Belly (豚の角煮)`), so travelers can point at their phone screen to order.",
                "7. **7 Standard Sections**: 🍜 Must-Try / Core Highlights, 👍 Strengths & Pros, 👎 Weaknesses & Red Flags, 💡 Operational Insights & Rules, 🤫 Insider Tips, 🚗 Transit & Parking, 🚨 Notable Callouts.",
                "8. **🔒 Post-Digest Vault Nudge**: At the end of the report, append a brief 1-line note: *\"💡 Want to save this place to your `wishlist` or mark it as `liked` / `disliked` in your Personal Dining Vault? Just let me know!\"*",
            ])

    return "\n".join(lines)


def format_comparison_markdown_output(result: dict, include_agent_instructions: bool = False) -> str:
    """Format multi-venue (--compare or --top N) result into a unified Markdown comparison/audit context."""
    mode = result.get("mode") or "compare"
    places = result.get("places") or []
    target_lang = str(result.get("preferred_summary_language") or "zh-TW")
    is_zh = target_lang.lower().startswith("zh")

    lines: list[str] = []
    if mode == "top_discovery":
        query_str = result.get("query") or ""
        sponsored_removed = result.get("sponsored_removed") or 0
        winner = result.get("recommended_winner") or {}
        if is_zh:
            lines.append(f"# 🗺️ 區域探索 Top-{len(places)} 掃描與防洗分稽核：「{query_str}」")
            lines.append(
                f"🛡️ 已自動剔除 {sponsored_removed} 筆贊助廣告 (Sponsored) | "
                f"🏆 防洗分推薦首選: **{winner.get('name') or 'N/A'}** "
                f"(真實有機評分: {winner.get('organic_score', 'N/A')} / 刷評嫌疑: {winner.get('suspicion_level', 'low')})"
            )
        else:
            lines.append(f"# 🗺️ Area Discovery Top-{len(places)} Scan & Anti-Manipulation Audit: \"{query_str}\"")
            lines.append(
                f"🛡️ Stripped {sponsored_removed} Sponsored Ads | "
                f"🏆 True Organic Winner: **{winner.get('name') or 'N/A'}** "
                f"(Organic Score: {winner.get('organic_score', 'N/A')} / Manipulation Risk: {winner.get('suspicion_level', 'low')})"
            )
    else:
        names = [str((item.get("place") or {}).get("name") or f"Store #{i + 1}") for i, item in enumerate(places)]
        if is_zh:
            lines.append(f"# ⚔️ 多店 PK 決策對比：{' vs. '.join(names)}")
        else:
            lines.append(f"# ⚔️ Multi-Venue Side-by-Side Comparison: {' vs. '.join(names)}")

    lines.append("")

    for idx, item in enumerate(places, 1):
        p = item.get("place") or {}
        s = item.get("stats") or {}
        audit = item.get("audit") or {}
        avg_r = p.get("average_rating")
        rating_str = f"⭐ {avg_r:.1f}" if avg_r is not None else "⭐ N/A"
        dist_m = (item.get("search") or {}).get("matched_distance_m")
        dist_str = f" | 📍 ~{dist_m}m" if dist_m is not None else ""
        price_str = f" | 💰 {p['price']}" if p.get("price") else ""
        open_str = f" | 🕒 {p['open_state']}" if p.get("open_state") else ""

        lines.append(f"## [{idx}] {p.get('name') or f'Venue {idx}'} ({rating_str}, {p.get('total_reviews') or 0} reviews{dist_str}{price_str}{open_str})")
        if p.get("address"):
            lines.append(f"- **{'地址' if is_zh else 'Address'}**: {p['address']}")
        if p.get("service_options"):
            lines.append(f"- **{'服務與設施' if is_zh else 'Services'}**: {' | '.join(p['service_options'])}")
        pt_line = _format_popular_times_line(p.get("popular_times") or {}, is_zh=is_zh)
        if pt_line:
            lines.append(f"- {pt_line}")
        if p.get("menu_urls"):
            lines.append(f"- **{'最新菜單圖片' if is_zh else 'Latest Menu Photos'}**: {' | '.join(p['menu_urls'])}")
        if audit:
            if is_zh:
                lines.append(
                    f"- **🛡️ 防洗分檢測**: 刷評關鍵字提及 {audit.get('promo_mentions', 0)} 次 | "
                    f"極端兩極分布: {'是' if audit.get('bimodal_warning') else '否'} | "
                    f"嫌疑等級: `{audit.get('suspicion_level', 'low')}` | "
                    f"有機加權分: **{audit.get('organic_score', 'N/A')}**"
                )
            else:
                lines.append(
                    f"- **🛡️ Manipulation Audit**: Promo mentions: {audit.get('promo_mentions', 0)} | "
                    f"Bimodal warning: {audit.get('bimodal_warning', False)} | "
                    f"Risk: `{audit.get('suspicion_level', 'low')}` | "
                    f"Organic Score: **{audit.get('organic_score', 'N/A')}**"
                )
        lines.append(f"- **{'精選評論抽樣' if is_zh else 'Curated Reviews'}** ({s.get('used_reviews') or 0} reviews, lang={s.get('fetch_language') or 'en'}):")
        lines.append(item.get("formatted_reviews") or "(No reviews)")
        lines.append("")

    if include_agent_instructions:
        if is_zh:
            lines.extend([
                "---",
                f"## 🦊 TL;DReview 多店決策與橫向比較規範 (請以 `{target_lang}` 輸出)",
                "請根據上方各店家的精選評論與防洗分稽核數據，輸出結構化 PK 決策報告：",
                "1. **📊 橫向 PK 對比表（Markdown Table）**：請建立對比表格，比較各店的：**評分與真實度（有無刷評）**、**🍜 必點招牌**、**👎 雷點與抱怨**、**⏳ 排隊難度与尖峰時段**、**💰 價格與營業狀態**、**🎯 適合誰**。",
                "2. **🛡️ 防洗分稽核與真實冠軍推薦**：揭露哪幾家有「五星好評送小菜/飲料」等洗分跡象，並明確推薦「🏆 真正零刷評 / 最值得去的冠軍店家」。",
                "3. **🗣️ 點餐手指卡**：若為跨語言查詢（如日/韓/泰/法文評論），必點菜色請保留當地語言原名對照（如 `炙燒和牛 (炙り和牛)`）。",
            ])
        else:
            lines.extend([
                "---",
                f"## 🦊 TL;DReview Multi-Venue Comparison & Audit Guidelines (Output in `{target_lang}`)",
                "Synthesize a side-by-side decision report strictly grounded in the venue evidence and manipulation audit above:",
                "1. **📊 Side-by-Side Comparison Table**: Build a Markdown table comparing each venue across: **Rating & Organic Trust**, **🍜 Must-Try Dishes**, **👎 Red Flags & Cons**, **⏳ Wait Time & Peak Hours**, **💰 Price & Open Status**, and **🎯 Best For**.",
                "2. **🛡️ Anti-Manipulation Audit & Winner Call**: Call out any venue using review-for-freebie promotions and crown the **🏆 True Organic Winner**.",
                "3. **🗣️ Traveler's Point-and-Speak**: If cross-language reviews were fetched, preserve original local-language dish names in parentheses next to translated names.",
            ])

    return "\n".join(lines)


CACHE_TTL_SECONDS = 24 * 60 * 60


def _cache_path(
    data_id: str,
    preferred_lang: Optional[str] = None,
    sort_by: str = "qualityScore",
) -> str:
    base = os.getenv("TLDREVIEW_CACHE_DIR") or os.path.join(
        os.path.expanduser("~"), ".cache", "tldreview"
    )
    raw_key = f"{data_id}__{preferred_lang}" if preferred_lang else data_id
    if sort_by and sort_by != "qualityScore":
        raw_key = f"{raw_key}__{sort_by}"
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", raw_key)
    return os.path.join(base, f"{safe}.json")


def _cache_load(path: str) -> Optional[dict]:
    try:
        if time.time() - os.path.getmtime(path) > CACHE_TTL_SECONDS:
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _cache_save(path: str, result: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)
    except OSError as exc:
        log(f"Cache write warning: {exc}")


def _merge_place_info(primary: PlaceInfo, secondary: PlaceInfo) -> PlaceInfo:
    """Merge two PlaceInfo instances, preferring non-empty fields from primary."""
    merged_menus: list[str] = []
    for u in (primary.menu_urls or []) + (secondary.menu_urls or []):
        if u not in merged_menus:
            merged_menus.append(u)
    return PlaceInfo(
        name=primary.name or secondary.name,
        address=primary.address or secondary.address,
        latitude=primary.latitude if primary.latitude is not None else secondary.latitude,
        longitude=primary.longitude if primary.longitude is not None else secondary.longitude,
        types=primary.types or secondary.types,
        rating_summary=primary.rating_summary or secondary.rating_summary,
        average_rating=primary.average_rating if primary.average_rating is not None else secondary.average_rating,
        total_reviews=primary.total_reviews if primary.total_reviews is not None else secondary.total_reviews,
        data_id=primary.data_id or secondary.data_id,
        place_id=primary.place_id or secondary.place_id,
        price=primary.price or secondary.price,
        open_state=primary.open_state or secondary.open_state,
        service_options=primary.service_options or secondary.service_options,
        popular_times=primary.popular_times or secondary.popular_times,
        menu_urls=merged_menus[:2],
    )


def run_pipeline(
    raw_input: str = "",
    api_key: str = "",
    preferred_lang: str = "zh-TW",
    use_cache: bool = True,
    sort_by: str = "qualityScore",
    save_quota: bool = False,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    ll: Optional[str] = None,
    zoom: int = 15,
    image_path: Optional[str] = None,
    max_reviews: int = MAX_REVIEWS_FOR_LLM,
    max_total_chars: int = MAX_TOTAL_REVIEW_CHARS,
    pre_resolved_place: Optional[Tuple[str, PlaceInfo, Optional[int]]] = None,
) -> dict:
    """Full execution pipeline supporting Google Maps URLs, keyword/nearby search, and photos."""
    canonical_sort = normalize_sort_by(sort_by)
    if not save_quota and os.getenv("TLDREVIEW_SAVE_QUOTA", "").strip().lower() in {"1", "true", "yes"}:
        save_quota = True

    # 0. Normalize coordinates from ll string or image EXIF GPS if present
    if ll and (lat is None or lng is None):
        parsed_ll = parse_ll_string(ll)
        if not parsed_ll:
            raise ValueError(f"Invalid coordinate string for ll: '{ll}'")
        lat, lng = parsed_ll[0], parsed_ll[1]
        if parsed_ll[2] is not None:
            zoom = parsed_ll[2]

    if image_path:
        if lat is None or lng is None:
            exif_coords = extract_exif_gps(image_path)
            if exif_coords:
                lat, lng = exif_coords
                log(f"Extracted EXIF GPS coordinates from image: ({lat}, {lng})")
        if not (raw_input or "").strip():
            ocr_data = extract_query_from_image(image_path)
            raw_input = str(ocr_data.get("search_query") or "").strip()
            log(f"Recognized store query from image: '{raw_input}'")

    text_input = (raw_input or "").strip()
    if not text_input and not pre_resolved_place:
        raise ValueError("No Google Maps URL, store name, or search query provided.")

    # 1. Check for pre-resolved candidate, Google Maps URL, or Keyword / Nearby Search
    resolved_url: Optional[str] = None
    data_id: Optional[str] = None
    searched_place_info: Optional[PlaceInfo] = None
    search_meta: Optional[dict[str, Any]] = None
    fallback_query: Optional[str] = None

    if pre_resolved_place is not None:
        data_id, searched_place_info, matched_dist = pre_resolved_place
        search_meta = {
            "query": text_input or (searched_place_info.name or ""),
            "cleaned_query": text_input or (searched_place_info.name or ""),
            "latitude": lat,
            "longitude": lng,
            "matched_distance_m": matched_dist,
            "candidates": [],
        }
    else:
        url = extract_google_maps_url(text_input)
        if url:
            resolved_url = resolve_short_url(url)
            data_id = extract_maps_data_id(resolved_url)
            if not data_id:
                url_coords = extract_coordinates(resolved_url)
                if url_coords and (lat is None or lng is None):
                    lat, lng = url_coords
                outside_text = text_input.replace(url, " ").strip()
                fallback_query = outside_text or extract_search_query_from_maps_url(resolved_url)
                if not fallback_query:
                    if url_coords:
                        raise ValueError(
                            f"URL contains coordinates ({url_coords}) but no place data_id. "
                            "Please provide a store name or use a full place share link."
                        )
                    raise ValueError(f"Unable to extract data_id or coordinates from URL: {resolved_url}")

        if not data_id:
            query_str = fallback_query if url else text_input
            if lat is None or lng is None:
                query_str, inline_coords = extract_inline_coordinates(query_str)
                if inline_coords:
                    lat, lng = inline_coords
            if not query_str:
                raise ValueError(f"No valid Google Maps URL or place search query found in input: {raw_input}")

            cleaned_q = clean_search_query(query_str, has_coords=(lat is not None and lng is not None))
            data_id, searched_place_info, candidates = search_serpapi_place(
                api_key=api_key,
                query=query_str,
                lat=lat,
                lng=lng,
                zoom=zoom,
                lang=preferred_lang,
            )
            matched_dist = candidates[0].get("distance_m") if candidates else None
            search_meta = {
                "query": text_input,
                "cleaned_query": cleaned_q,
                "latitude": lat,
                "longitude": lng,
                "image_path": image_path,
                "matched_distance_m": matched_dist,
                "candidates": candidates,
            }

    log(f"Target data_id: {data_id}")

    is_default_budget = (max_reviews == MAX_REVIEWS_FOR_LLM and max_total_chars == MAX_TOTAL_REVIEW_CHARS)
    cache_file = _cache_path(data_id, sort_by=canonical_sort)
    if use_cache and is_default_budget:
        cached = _cache_load(cache_file) or _cache_load(
            _cache_path(data_id, preferred_lang, sort_by=canonical_sort)
        )
        if cached:
            log("Cache hit (< 24h) - skipping SerpApi review calls")
            cached["cached"] = True
            if isinstance(cached.get("stats"), dict):
                cached["stats"]["preferred_summary_language"] = preferred_lang
                cached["stats"]["cross_language"] = _is_cross_language(
                    cached["stats"].get("fetch_language"), preferred_lang
                )
            if search_meta:
                cached["search"] = search_meta
            return cached

    # 4. Fetch place details (skipped when save_quota is active or search already returned full info)
    if searched_place_info is not None:
        if save_quota or searched_place_info.rating_summary:
            place_info = searched_place_info
        else:
            details_info = fetch_serpapi_place_details(api_key, data_id)
            place_info = _merge_place_info(details_info, searched_place_info)
        official_lang = infer_language(place_info.address) if place_info.address else (preferred_lang or "en").lower()
        log(f"Detected place: '{place_info.name or 'Unknown'}' in language '{official_lang}'")
        review_id = data_id
        if not is_reviews_ready_id(review_id):
            review_id = place_info.data_id or place_info.place_id or data_id
    elif save_quota and is_reviews_ready_id(data_id):
        log("Save-quota mode enabled: skipping place details call (1 SerpApi credit mode)")
        place_info = PlaceInfo()
        official_lang = (preferred_lang or "en").lower()
        review_id = data_id
    else:
        place_info = fetch_serpapi_place_details(api_key, data_id)
        official_lang = infer_language(place_info.address)
        log(f"Detected place: '{place_info.name or 'Unknown'}' in language '{official_lang}'")
        review_id = data_id
        if not is_reviews_ready_id(review_id):
            review_id = place_info.data_id or place_info.place_id or data_id

    # 5. Fetch reviews in official language
    fetch_lang = official_lang
    reviews, total_count, next_token, raw_payload = fetch_serpapi_reviews(
        api_key=api_key,
        data_id=review_id,
        lang=fetch_lang,
        sort_by=canonical_sort,
    )

    # Merge place info if first call was sparse or skipped
    if isinstance(raw_payload.get("place_info"), dict):
        p_from_rev = _parse_place_dict(raw_payload["place_info"])
        place_info = _merge_place_info(place_info, p_from_rev)

    # Fetch second page if total reviews are large (>500), token exists, and not in save_quota mode
    if not save_quota and next_token and (total_count or 0) > NEXT_PAGE_THRESHOLD:
        try:
            more_reviews, _, _, _ = fetch_serpapi_reviews(
                api_key=api_key,
                data_id=review_id,
                lang=fetch_lang,
                page_token=next_token,
                sort_by=canonical_sort,
            )
            seen = {r.dedup_key() for r in reviews}
            for mr in more_reviews:
                key = mr.dedup_key()
                if key not in seen:
                    reviews.append(mr)
                    seen.add(key)
            log(f"Merged second page -> {len(reviews)} total fetched reviews")
        except Exception as exc:
            log(f"Second page fetch warning: {exc}")

    # English refetch if recent (<2y) local guide coverage is thin, total reviews >= 10, and not in save_quota mode
    cutoff = datetime.now(timezone.utc) - RECENT_MAX_AGE
    local_recent_guides = [
        r for r in reviews if r.is_local_guide and _is_recent(r, cutoff)
    ]
    if (not save_quota
            and len(local_recent_guides) < MIN_RECENT_LOCAL_GUIDES
            and (total_count or 0) >= MIN_TOTAL_FOR_EN_REFETCH
            and fetch_lang != "en"):
        try:
            log("Local Guide coverage is thin; refetching in English for international insights...")
            en_reviews, _, _, _ = fetch_serpapi_reviews(
                api_key=api_key,
                data_id=review_id,
                lang="en",
                sort_by=canonical_sort,
            )
            # Merge (instead of replacing) so local-language / page-2 reviews aren't discarded
            seen = {r.dedup_key() for r in reviews}
            added = 0
            for er in en_reviews:
                key = er.dedup_key()
                if key not in seen:
                    reviews.append(er)
                    seen.add(key)
                    added += 1
            if added:
                fetch_lang = f"{fetch_lang}+en"
        except Exception as exc:
            log(f"English refetch warning: {exc}")

    # Extract newest 1-2 menu/price photo URLs from place info and reviews
    place_info.menu_urls = _extract_menu_urls_from_reviews(reviews, existing=place_info.menu_urls)

    if not reviews:
        no_rev_res: dict[str, Any] = {
            "status": "no_reviews",
            "message": "This location does not have any public reviews on Google Maps.",
            "data_id": data_id,
            "place": asdict(place_info),
        }
        if search_meta:
            no_rev_res["search"] = search_meta
        return no_rev_res

    # 6. Curate and clip
    curated = curate_reviews(reviews)
    clipped = clip_reviews_for_prompt(
        curated, max_reviews=max_reviews, max_total_chars=max_total_chars
    )
    cross_lang = _is_cross_language(fetch_lang, preferred_lang)

    reviews_dicts = [
        {
            "author": r.author,
            "rating": r.rating,
            "relative_time": r.relative_time,
            "is_local_guide": r.is_local_guide,
            "like_count": r.like_count,
            "text": text,
            **({"images": r.images} if r.images else {}),
        }
        for r, text in clipped
    ]

    result: dict[str, Any] = {
        "status": "success",
        "data_id": data_id,
        "resolved_url": resolved_url,
        "place": {
            "name": place_info.name or "Google Maps Place",
            "address": place_info.address,
            "type": place_info.types[0] if place_info.types else "unknown",
            "all_types": place_info.types,
            "average_rating": place_info.average_rating,
            "total_reviews": total_count or place_info.total_reviews or len(reviews),
            "rating_summary": place_info.rating_summary,
            "price": place_info.price,
            "open_state": place_info.open_state,
            "service_options": place_info.service_options,
            "popular_times": place_info.popular_times,
            "menu_urls": place_info.menu_urls,
        },
        "stats": {
            "fetched_reviews": len(reviews),
            "curated_reviews": len(curated),
            "used_reviews": len(clipped),
            "fetch_language": fetch_lang,
            "preferred_summary_language": preferred_lang,
            "cross_language": cross_lang,
            "sort_by": canonical_sort,
            "save_quota": save_quota,
        },
        "formatted_reviews": format_reviews_block(clipped),
        "reviews": reviews_dicts,
    }
    if search_meta:
        result["search"] = search_meta
    if is_default_budget:
        _cache_save(cache_file, result)
    return result


def run_compare_pipeline(
    targets: list[str],
    api_key: str,
    preferred_lang: str = "zh-TW",
    use_cache: bool = True,
    sort_by: str = "qualityScore",
    save_quota: bool = False,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    ll: Optional[str] = None,
    zoom: int = 15,
    max_reviews_per_place: int = COMPARE_MAX_REVIEWS_PER_PLACE,
    max_chars_per_place: int = COMPARE_MAX_CHARS_PER_PLACE,
) -> dict:
    """Fetch, curate, and audit multiple venues in parallel with scaled token budgets for side-by-side comparison."""
    clean_targets = [t.strip() for t in targets if t and t.strip()]
    if len(clean_targets) < 2:
        raise ValueError("Compare mode (--compare) requires at least 2 URLs or store queries.")

    def _fetch_one(target_str: str) -> dict:
        res = run_pipeline(
            raw_input=target_str,
            api_key=api_key,
            preferred_lang=preferred_lang,
            use_cache=use_cache,
            sort_by=sort_by,
            save_quota=save_quota,
            lat=lat,
            lng=lng,
            ll=ll,
            zoom=zoom,
            max_reviews=max_reviews_per_place,
            max_total_chars=max_chars_per_place,
        )
        p = res.get("place") or {}
        res["audit"] = _audit_review_manipulation(
            res.get("reviews") or [],
            rating_summary=p.get("rating_summary"),
            average_rating=p.get("average_rating"),
        )
        return res

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(clean_targets), 5)) as pool:
        place_results = list(pool.map(_fetch_one, clean_targets))

    return {
        "status": "success",
        "mode": "compare",
        "preferred_summary_language": preferred_lang,
        "count": len(place_results),
        "token_budget_per_place": {
            "max_reviews": max_reviews_per_place,
            "max_total_chars": max_chars_per_place,
        },
        "places": place_results,
    }


def run_top_discovery_pipeline(
    query: str,
    api_key: str,
    top_n: int = 3,
    preferred_lang: str = "zh-TW",
    use_cache: bool = True,
    sort_by: str = "qualityScore",
    save_quota: bool = False,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    ll: Optional[str] = None,
    zoom: int = 15,
    max_reviews_per_place: int = COMPARE_MAX_REVIEWS_PER_PLACE,
    max_chars_per_place: int = COMPARE_MAX_CHARS_PER_PLACE,
) -> dict:
    """Search nearby top N organic places, fetch reviews in parallel, and crown the true organic winner."""
    if ll and (lat is None or lng is None):
        parsed_ll = parse_ll_string(ll)
        if not parsed_ll:
            raise ValueError(f"Invalid coordinate string for ll: '{ll}'")
        lat, lng = parsed_ll[0], parsed_ll[1]
        if parsed_ll[2] is not None:
            zoom = parsed_ll[2]

    query_str = (query or "").strip()
    if lat is None or lng is None:
        query_str, inline_coords = extract_inline_coordinates(query_str)
        if inline_coords:
            lat, lng = inline_coords

    cleaned_query, selected_candidates, sponsored_removed = search_serpapi_top_places(
        api_key=api_key,
        query=query_str,
        top_n=top_n,
        lat=lat,
        lng=lng,
        zoom=zoom,
        lang=preferred_lang,
    )

    def _fetch_cand(cand: Tuple[str, PlaceInfo, Optional[int]]) -> dict:
        cid, p_info, dist_m = cand
        res = run_pipeline(
            raw_input=p_info.name or cleaned_query,
            api_key=api_key,
            preferred_lang=preferred_lang,
            use_cache=use_cache,
            sort_by=sort_by,
            save_quota=save_quota,
            lat=lat,
            lng=lng,
            zoom=zoom,
            max_reviews=max_reviews_per_place,
            max_total_chars=max_chars_per_place,
            pre_resolved_place=(cid, p_info, dist_m),
        )
        p = res.get("place") or {}
        res["audit"] = _audit_review_manipulation(
            res.get("reviews") or [],
            rating_summary=p.get("rating_summary"),
            average_rating=p.get("average_rating"),
        )
        return res

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(selected_candidates), 5)) as pool:
        place_results = list(pool.map(_fetch_cand, selected_candidates))

    # Crown the True Organic Winner (highest organic_score, tie-broken by lowest promo_mentions & highest review count)
    recommended_winner: dict[str, Any] = {}
    if place_results:
        best = max(
            place_results,
            key=lambda item: (
                (item.get("audit") or {}).get("organic_score", 0.0),
                -((item.get("audit") or {}).get("promo_mentions", 0)),
                ((item.get("place") or {}).get("total_reviews") or 0),
            ),
        )
        bp = best.get("place") or {}
        ba = best.get("audit") or {}
        recommended_winner = {
            "name": bp.get("name"),
            "data_id": best.get("data_id"),
            "average_rating": bp.get("average_rating"),
            "organic_score": ba.get("organic_score"),
            "suspicion_level": ba.get("suspicion_level"),
            "promo_mentions": ba.get("promo_mentions"),
        }

    return {
        "status": "success",
        "mode": "top_discovery",
        "query": cleaned_query,
        "latitude": lat,
        "longitude": lng,
        "sponsored_removed": sponsored_removed,
        "preferred_summary_language": preferred_lang,
        "count": len(place_results),
        "recommended_winner": recommended_winner,
        "places": place_results,
    }


# ---------------------------------------------------------------------------
# Step 5: Zero-Dependency E2EE Cloud Vault (Personal Dining Memory)
# ---------------------------------------------------------------------------
VAULT_API_DEFAULT_BASE = "https://tldreview-vault-relay.cloudflare-d19.workers.dev/v1/vault"
VAULT_HTTP_TIMEOUT = 10.0
VALID_VAULT_STATUSES = ("wishlist", "liked", "disliked")
_VAULT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{12,32}$")
_VAULT_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-"


class VaultError(ValueError):
    """Base exception for TL;DReview Personal Dining Vault operations."""


class VaultDecryptionError(VaultError):
    """Raised when E2EE envelope HMAC verification or decryption fails."""


def generate_sync_key() -> str:
    """Generate a high-entropy E2EE Sync Key: tld_<vault_id>_<master_secret>."""
    # Generate 16-char vault_id without underscores so splitting on '_' is unambiguous
    vault_id = "".join(secrets.choice(_VAULT_ALPHABET) for _ in range(16))
    master_secret = secrets.token_urlsafe(32)
    return f"tld_{vault_id}_{master_secret}"


def parse_sync_key(sync_key: str) -> Tuple[str, str]:
    """Parse and validate a Sync Key into (vault_id, master_secret)."""
    raw = (sync_key or "").strip()
    if not raw.startswith("tld_"):
        raise VaultError("Invalid Sync Key format: must start with 'tld_'.")
    body = raw[4:]
    # Handle standard 16-char vault_id even if a custom vault_id contained an underscore
    if len(body) > 17 and body[16] == "_":
        vault_id = body[:16]
        master_secret = body[17:]
    elif "_" in body:
        vault_id, master_secret = body.split("_", 1)
    else:
        raise VaultError("Invalid Sync Key format: expected 'tld_<vault_id>_<master_secret>'.")

    if not _VAULT_ID_RE.fullmatch(vault_id):
        raise VaultError(f"Invalid vault_id '{vault_id}' in Sync Key (must be 12-32 URL-safe chars).")
    if len(master_secret) < 16:
        raise VaultError("Invalid master_secret in Sync Key (must be at least 16 characters).")
    return vault_id, master_secret


def derive_write_token(sync_key_or_vault_id: str, master_secret: Optional[str] = None) -> str:
    """Derive the server authentication Bearer token from a Sync Key or (vault_id, master_secret)."""
    if master_secret is None:
        vault_id, master_secret = parse_sync_key(sync_key_or_vault_id)
    else:
        vault_id = sync_key_or_vault_id
    return hmac.new(
        master_secret.encode("utf-8"),
        b"tldreview-auth-v1:" + vault_id.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def derive_subkeys(master_secret: str, nonce: bytes) -> Tuple[bytes, bytes]:
    """Derive 32-byte encryption key and 32-byte MAC key from master_secret and 16-byte nonce."""
    if len(nonce) != 16:
        raise ValueError("Encryption nonce must be exactly 16 bytes.")
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        master_secret.encode("utf-8"),
        nonce,
        iterations=100_000,
        dklen=64,
    )
    return dk[:32], dk[32:64]


def _blake2b_ctr_xor(data: bytes, enc_key: bytes, nonce: bytes) -> bytes:
    """XOR data with a BLAKE2b counter-mode keystream (64 bytes per block)."""
    if not data:
        return b""
    out = bytearray(len(data))
    num_blocks = (len(data) + 63) // 64
    for i in range(num_blocks):
        ks_block = hashlib.blake2b(
            nonce + i.to_bytes(8, "big"),
            key=enc_key,
            digest_size=64,
        ).digest()
        start = i * 64
        chunk = data[start : start + 64]
        for j, b in enumerate(chunk):
            out[start + j] = b ^ ks_block[j]
    return bytes(out)


def encrypt_vault_payload(plaintext_obj: dict[str, Any], sync_key: str) -> dict[str, Any]:
    """Compress and encrypt a plaintext Vault dict into a v1 E2EE JSON envelope."""
    _, master_secret = parse_sync_key(sync_key)
    plaintext_bytes = json.dumps(
        plaintext_obj, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    compressed = zlib.compress(plaintext_bytes)
    nonce = secrets.token_bytes(16)
    enc_key, mac_key = derive_subkeys(master_secret, nonce)
    ciphertext = _blake2b_ctr_xor(compressed, enc_key, nonce)
    tag = hmac.new(mac_key, b"v1" + nonce + ciphertext, hashlib.sha256).digest()
    return {
        "v": 1,
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ct": base64.b64encode(ciphertext).decode("ascii"),
        "tag": base64.b64encode(tag).decode("ascii"),
        "updated_at": int(time.time()),
    }


def decrypt_vault_payload(envelope: dict[str, Any], sync_key: str) -> dict[str, Any]:
    """Verify HMAC and decrypt a v1 E2EE JSON envelope back into a plaintext Vault dict."""
    if not isinstance(envelope, dict) or envelope.get("v") != 1:
        raise VaultDecryptionError("Unsupported or malformed vault envelope version.")

    try:
        nonce = base64.b64decode(str(envelope["nonce"]), validate=True)
        ciphertext = base64.b64decode(str(envelope["ct"]), validate=True)
        received_tag = base64.b64decode(str(envelope["tag"]), validate=True)
    except Exception as exc:
        raise VaultDecryptionError(f"Invalid base64 encoding in vault envelope: {exc}") from None

    if len(nonce) != 16 or len(received_tag) != 32:
        raise VaultDecryptionError("Invalid nonce or MAC tag length in vault envelope.")

    _, master_secret = parse_sync_key(sync_key)
    enc_key, mac_key = derive_subkeys(master_secret, nonce)
    expected_tag = hmac.new(mac_key, b"v1" + nonce + ciphertext, hashlib.sha256).digest()

    if not hmac.compare_digest(expected_tag, received_tag):
        raise VaultDecryptionError(
            "Vault HMAC verification failed: ciphertext/nonce was tampered with or Sync Key is incorrect."
        )

    try:
        compressed = _blake2b_ctr_xor(ciphertext, enc_key, nonce)
        plaintext_bytes = zlib.decompress(compressed)
        data = json.loads(plaintext_bytes.decode("utf-8"))
    except Exception as exc:
        raise VaultDecryptionError(f"Failed to decompress or parse decrypted vault payload: {exc}") from None

    if not isinstance(data, dict):
        raise VaultDecryptionError("Decrypted vault payload is not a JSON object.")
    return data


def _vault_base_dir() -> str:
    return os.getenv("TLDREVIEW_VAULT_DIR") or os.path.join(
        os.path.expanduser("~"), ".tldreview"
    )


def get_vault_path() -> str:
    """Return local plaintext cache path (~/.tldreview/vault.json or TLDREVIEW_VAULT_PATH)."""
    return os.getenv("TLDREVIEW_VAULT_PATH") or os.path.join(_vault_base_dir(), "vault.json")


def get_sync_key_path() -> str:
    """Return local sync key file path (~/.tldreview/sync.key or TLDREVIEW_SYNC_KEY_PATH)."""
    return os.getenv("TLDREVIEW_SYNC_KEY_PATH") or os.path.join(_vault_base_dir(), "sync.key")


def _persist_sync_key_file(sync_key: str) -> None:
    key_path = get_sync_key_path()
    try:
        os.makedirs(os.path.dirname(key_path), exist_ok=True)
        with open(key_path, "w", encoding="utf-8") as f:
            f.write(sync_key.strip() + "\n")
        try:
            os.chmod(key_path, 0o600)
        except OSError:
            pass
    except OSError as exc:
        log(f"Warning: unable to save sync key to {key_path}: {exc}")


def resolve_sync_key(
    explicit_key: Optional[str] = None,
    auto_create: bool = False,
) -> Tuple[Optional[str], bool]:
    """Resolve Sync Key from CLI arg -> TLDREVIEW_SYNC_KEY env -> ~/.tldreview/sync.key -> auto-generate."""
    if explicit_key and explicit_key.strip():
        clean = explicit_key.strip()
        parse_sync_key(clean)
        _persist_sync_key_file(clean)
        return clean, False

    load_dotenv_if_present()
    env_key = os.getenv("TLDREVIEW_SYNC_KEY", "").strip()
    if env_key:
        parse_sync_key(env_key)
        return env_key, False

    key_path = get_sync_key_path()
    if os.path.isfile(key_path):
        try:
            with open(key_path, "r", encoding="utf-8") as f:
                saved = f.read().strip()
            if saved:
                parse_sync_key(saved)
                return saved, False
        except (OSError, ValueError):
            pass

    if auto_create:
        new_key = generate_sync_key()
        _persist_sync_key_file(new_key)
        return new_key, True

    return None, False


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _empty_vault() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "updated_at": _now_iso_utc(),
        "sync_pending": False,
        "places": {},
    }


def load_local_vault(vault_path: Optional[str] = None) -> dict[str, Any]:
    """Load local vault.json or return an initialized empty vault schema."""
    path = vault_path or get_vault_path()
    if not os.path.isfile(path):
        return _empty_vault()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("places"), dict):
            data.setdefault("schema_version", 1)
            data.setdefault("updated_at", _now_iso_utc())
            data.setdefault("sync_pending", False)
            return data
    except (OSError, ValueError) as exc:
        log(f"Warning: could not read local vault {path}: {exc}")
    return _empty_vault()


def save_local_vault(vault_data: dict[str, Any], vault_path: Optional[str] = None) -> str:
    """Persist vault data to ~/.tldreview/vault.json with 0600 permissions."""
    path = vault_path or get_vault_path()
    vault_data["schema_version"] = 1
    vault_data["updated_at"] = _now_iso_utc()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(vault_data, f, ensure_ascii=False, indent=2)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def _vault_api_url(vault_id: str) -> str:
    base = (os.getenv("TLDREVIEW_VAULT_API_URL") or VAULT_API_DEFAULT_BASE).rstrip("/")
    return f"{base}/{urllib.parse.quote(vault_id)}"


def _vault_health_url() -> str:
    base = (os.getenv("TLDREVIEW_VAULT_API_URL") or VAULT_API_DEFAULT_BASE).rstrip("/")
    if base.endswith("/v1/vault"):
        return base[: -len("/v1/vault")] + "/health"
    return f"{base}/health"


VAULT_MAX_RETRIES = 3
_LAST_VAULT_SYNC_DIAG: dict[str, Any] = {}


def _classify_vault_error(exc: Exception, url: str = "") -> dict[str, Any]:
    """Classify a Cloud Vault HTTP/network exception into actionable diagnostics and retryability."""
    if isinstance(exc, urllib.error.HTTPError):
        code = exc.code
        err_body = ""
        try:
            err_body = exc.read().decode("utf-8", errors="replace").strip()[:240]
        except Exception:
            pass
        detail = err_body or str(exc.reason or exc)
        if code in (401, 403):
            return {
                "error_type": "auth_error",
                "http_status": code,
                "retryable": False,
                "message": f"HTTP {code} Unauthorized/Forbidden: {detail}",
                "hint": (
                    "設定錯誤（重試無效）：目前的 TLDREVIEW_SYNC_KEY 派生的 write_token 與雲端該金庫建立時不一致。"
                    "請確認環境變數 TLDREVIEW_SYNC_KEY 或 ~/.tldreview/sync.key 是否正確。"
                ),
            }
        if code == 413:
            return {
                "error_type": "payload_too_large",
                "http_status": code,
                "retryable": False,
                "message": f"HTTP 413 Payload Too Large: {detail}",
                "hint": "金庫加密後超過雲端單筆上限 (512KB)，請使用 --vault-delete 清理部分舊紀錄。",
            }
        if code == 429:
            return {
                "error_type": "rate_limit",
                "http_status": code,
                "retryable": True,
                "message": f"HTTP 429 Too Many Requests: {detail}",
                "hint": "請求過於頻繁，請稍後執行 --vault-sync 重試。",
            }
        if 400 <= code < 500:
            return {
                "error_type": "client_error",
                "http_status": code,
                "retryable": False,
                "message": f"HTTP {code} Client Error: {detail}",
                "hint": "請求格式或 TLDREVIEW_VAULT_API_URL 路由設定錯誤（重試無效），請執行 --vault-status 檢查設定。",
            }
        return {
            "error_type": "server_error",
            "http_status": code,
            "retryable": True,
            "message": f"HTTP {code} Server Error: {detail}",
            "hint": "Cloudflare Worker 暫時異常或 KV 尚未綁定，請稍後執行 --vault-sync 重試。",
        }

    reason = getattr(exc, "reason", exc)
    exc_str = str(exc)
    is_timeout = (
        isinstance(exc, TimeoutError)
        or isinstance(reason, TimeoutError)
        or "timed out" in exc_str.lower()
        or "timeout" in exc_str.lower()
    )
    if is_timeout:
        return {
            "error_type": "timeout",
            "http_status": None,
            "retryable": True,
            "message": f"Network timeout ({VAULT_HTTP_TIMEOUT}s) connecting to {url or 'Cloud Vault'}: {exc}",
            "hint": "網路連線超時（已自動重試）。可能是暫時性網路抖動或 Worker 冷啟動，稍後執行 --vault-sync 即可補推上雲端。",
        }

    is_local_refused = isinstance(reason, ConnectionRefusedError) or "127.0.0.1" in url or "localhost" in url
    return {
        "error_type": "network_blocked",
        "http_status": None,
        "retryable": not is_local_refused,
        "message": f"Network/DNS or sandbox firewall blocked connection to {url or 'Cloud Vault'}: {exc}",
        "hint": (
            "無法連線至雲端中繼站。若在 AI 沙盒環境（如 Meta Muse / Grok）執行，"
            "請確認已開通網域 tldreview-vault-relay.cloudflare-d19.workers.dev 的外網權限，或執行 --vault-status 診斷。"
        ),
    }


def _cloud_vault_put(vault_data: dict[str, Any], sync_key: str) -> bool:
    """Encrypt and PUT vault data to Cloudflare Worker KV relay with automatic retry. Returns True on success."""
    global _LAST_VAULT_SYNC_DIAG
    _LAST_VAULT_SYNC_DIAG = {}

    if os.getenv("TLDREVIEW_VAULT_OFFLINE", "").strip().lower() in {"1", "true", "yes"}:
        _LAST_VAULT_SYNC_DIAG = {
            "error_type": "offline_mode",
            "http_status": None,
            "retryable": False,
            "attempts": 0,
            "message": "TLDREVIEW_VAULT_OFFLINE is enabled.",
            "hint": "目前處於強制離線模式（TLDREVIEW_VAULT_OFFLINE=1），僅儲存於本地快取。",
        }
        return False

    vault_id, _ = parse_sync_key(sync_key)
    write_token = derive_write_token(sync_key)
    clean_payload = {
        "schema_version": vault_data.get("schema_version", 1),
        "updated_at": vault_data.get("updated_at") or _now_iso_utc(),
        "places": vault_data.get("places") or {},
    }
    envelope = encrypt_vault_payload(clean_payload, sync_key)
    body_bytes = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
    target_url = _vault_api_url(vault_id)

    max_attempts = VAULT_MAX_RETRIES
    for attempt in range(1, max_attempts + 1):
        req = urllib.request.Request(
            target_url,
            data=body_bytes,
            headers={
                "Authorization": f"Bearer {write_token}",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
            method="PUT",
        )
        try:
            with urllib.request.urlopen(req, timeout=VAULT_HTTP_TIMEOUT) as resp:
                if resp.status in (200, 201):
                    _LAST_VAULT_SYNC_DIAG = {}
                    return True
        except Exception as exc:
            diag = _classify_vault_error(exc, url=target_url)
            diag["attempts"] = attempt
            _LAST_VAULT_SYNC_DIAG = diag
            if diag.get("retryable") and attempt < max_attempts:
                backoff_s = 0.6 * (2 ** (attempt - 1))
                log(
                    f"Cloud Vault PUT transient error ({diag['error_type']}, attempt {attempt}/{max_attempts}), "
                    f"retrying in {backoff_s:.1f}s: {diag['message']}"
                )
                time.sleep(backoff_s)
                continue
            log(
                f"Cloud Vault sync failed [{diag['error_type']}] after {attempt} attempt(s) (saved locally): "
                f"{diag['message']} | Hint: {diag['hint']}"
            )
            return False
    return False


def _cloud_vault_get(sync_key: str) -> Optional[dict[str, Any]]:
    """GET and decrypt remote vault from Cloudflare Worker KV relay with automatic retry. Returns None if 404 or unreachable."""
    global _LAST_VAULT_SYNC_DIAG
    if os.getenv("TLDREVIEW_VAULT_OFFLINE", "").strip().lower() in {"1", "true", "yes"}:
        return None
    vault_id, _ = parse_sync_key(sync_key)
    target_url = _vault_api_url(vault_id)

    max_attempts = VAULT_MAX_RETRIES
    envelope: Optional[dict[str, Any]] = None
    for attempt in range(1, max_attempts + 1):
        req = urllib.request.Request(
            target_url,
            headers={
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=VAULT_HTTP_TIMEOUT) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                envelope = json.loads(raw)
                break
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            diag = _classify_vault_error(exc, url=target_url)
            diag["attempts"] = attempt
            _LAST_VAULT_SYNC_DIAG = diag
            if diag.get("retryable") and attempt < max_attempts:
                backoff_s = 0.6 * (2 ** (attempt - 1))
                log(
                    f"Cloud Vault GET transient HTTP {exc.code} (attempt {attempt}/{max_attempts}), "
                    f"retrying in {backoff_s:.1f}s..."
                )
                time.sleep(backoff_s)
                continue
            log(f"Cloud Vault GET failed [{diag['error_type']}]: {diag['message']}")
            return None
        except Exception as exc:
            diag = _classify_vault_error(exc, url=target_url)
            diag["attempts"] = attempt
            _LAST_VAULT_SYNC_DIAG = diag
            if diag.get("retryable") and attempt < max_attempts:
                backoff_s = 0.6 * (2 ** (attempt - 1))
                log(
                    f"Cloud Vault GET transient error ({diag['error_type']}, attempt {attempt}/{max_attempts}), "
                    f"retrying in {backoff_s:.1f}s..."
                )
                time.sleep(backoff_s)
                continue
            log(f"Cloud Vault GET unreachable [{diag['error_type']}]: {diag['message']}")
            return None

    if envelope is None:
        return None
    return decrypt_vault_payload(envelope, sync_key)


def _split_csv_items(val: Any) -> list[str]:
    """Parse a comma/頓號/semicolon-separated string or list of strings into clean items."""
    if not val:
        return []
    if isinstance(val, list):
        raw_items = [str(x) for x in val]
    else:
        raw_items = re.split(r"[,，、;\n]+", str(val))
    result: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        clean = item.strip()
        if clean and clean.casefold() not in seen:
            seen.add(clean.casefold())
            result.append(clean)
    return result


def _merge_unique_str_list(existing: Optional[list[str]], incoming: Optional[list[str]]) -> list[str]:
    """Merge two lists of strings preserving order and case-insensitive uniqueness."""
    merged: list[str] = []
    seen: set[str] = set()
    for item in (existing or []) + (incoming or []):
        clean = str(item or "").strip()
        if clean and clean.casefold() not in seen:
            seen.add(clean.casefold())
            merged.append(clean)
    return merged


def _merge_place_records(old_rec: dict[str, Any], new_rec: dict[str, Any]) -> dict[str, Any]:
    """Merge an incoming place record into an existing place record per Upsert & Deduplication rules."""
    status = str(new_rec.get("status") or old_rec.get("status") or "wishlist").lower()
    if status not in VALID_VAULT_STATUSES:
        status = "wishlist"

    now_iso = _now_iso_utc()
    old_visited = old_rec.get("visited_at")
    new_visited = new_rec.get("visited_at")
    if status in ("liked", "disliked"):
        visited_at = new_visited or old_visited or now_iso
        if old_rec.get("status") == "wishlist":
            visited_at = new_visited or now_iso
    else:
        visited_at = new_visited if new_visited is not None else old_visited

    old_notes = str(old_rec.get("user_notes") or "").strip()
    inc_notes = str(new_rec.get("user_notes") or "").strip()
    if inc_notes and old_notes and inc_notes != old_notes and inc_notes not in old_notes:
        merged_notes = f"{old_notes} | {inc_notes}"
    else:
        merged_notes = inc_notes or old_notes

    return {
        "place_id": new_rec.get("place_id") or old_rec.get("place_id"),
        "name": new_rec.get("name") or old_rec.get("name") or "Unknown Place",
        "address": new_rec.get("address") or old_rec.get("address"),
        "lat": new_rec.get("lat") if new_rec.get("lat") is not None else old_rec.get("lat"),
        "lng": new_rec.get("lng") if new_rec.get("lng") is not None else old_rec.get("lng"),
        "status": status,
        "user_rating": (
            new_rec.get("user_rating")
            if new_rec.get("user_rating") is not None
            else old_rec.get("user_rating")
        ),
        "google_rating": (
            new_rec.get("google_rating")
            if new_rec.get("google_rating") is not None
            else old_rec.get("google_rating")
        ),
        "tags": _merge_unique_str_list(old_rec.get("tags"), new_rec.get("tags")),
        "must_try": _merge_unique_str_list(old_rec.get("must_try"), new_rec.get("must_try")),
        "red_flags": _merge_unique_str_list(old_rec.get("red_flags"), new_rec.get("red_flags")),
        "insider_tips": _merge_unique_str_list(old_rec.get("insider_tips"), new_rec.get("insider_tips")),
        "user_notes": merged_notes,
        "url": new_rec.get("url") or old_rec.get("url"),
        "saved_at": new_rec.get("saved_at") or now_iso,
        "visited_at": visited_at,
    }


def export_obsidian_markdown(place_rec: dict[str, Any], obsidian_dir: Optional[str] = None) -> Optional[str]:
    """Write a Markdown note with YAML frontmatter to TLDREVIEW_OBSIDIAN_DIR if configured."""
    target_dir = (obsidian_dir or os.getenv("TLDREVIEW_OBSIDIAN_DIR") or "").strip()
    if not target_dir:
        return None
    raw_name = str(place_rec.get("name") or place_rec.get("place_id") or "place").strip()
    safe_name = re.sub(r'[\\/:*?"<>|\r\n]+', "_", raw_name).strip(" ._") or "place"
    os.makedirs(target_dir, exist_ok=True)
    file_path = os.path.join(target_dir, f"{safe_name}.md")

    tags_yaml = json.dumps(place_rec.get("tags") or [], ensure_ascii=False)
    frontmatter = [
        "---",
        f'place_id: "{place_rec.get("place_id") or ""}"',
        f'name: "{raw_name}"',
        f'status: "{place_rec.get("status") or "wishlist"}"',
        f"user_rating: {json.dumps(place_rec.get('user_rating'))}",
        f"google_rating: {json.dumps(place_rec.get('google_rating'))}",
        f"lat: {json.dumps(place_rec.get('lat'))}",
        f"lng: {json.dumps(place_rec.get('lng'))}",
        f"tags: {tags_yaml}",
        f'saved_at: "{place_rec.get("saved_at") or ""}"',
        f"visited_at: {json.dumps(place_rec.get('visited_at'))}",
        "---",
        "",
        f"# {raw_name}",
        "",
    ]
    if place_rec.get("address"):
        frontmatter.append(f"- **Address**: {place_rec['address']}")
    if place_rec.get("url"):
        frontmatter.append(f"- **Google Maps**: {place_rec['url']}")
    if place_rec.get("must_try"):
        frontmatter.append(f"- **Must-Try**: {', '.join(place_rec['must_try'])}")
    if place_rec.get("red_flags"):
        frontmatter.append(f"- **Red Flags**: {', '.join(place_rec['red_flags'])}")
    if place_rec.get("insider_tips"):
        frontmatter.append(f"- **Insider Tips**: {', '.join(place_rec['insider_tips'])}")
    if place_rec.get("user_notes"):
        frontmatter.extend(["", "## Personal Notes", str(place_rec["user_notes"])])

    with open(file_path, "w", encoding="utf-8") as f:
        f.write("\n".join(frontmatter) + "\n")
    return file_path


def vault_save_place(
    place_id: Optional[str] = None,
    name: Optional[str] = None,
    address: Optional[str] = None,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    google_rating: Optional[float] = None,
    status: str = "wishlist",
    user_rating: Optional[float] = None,
    tags: Any = None,
    must_try: Any = None,
    red_flags: Any = None,
    insider_tips: Any = None,
    user_notes: Optional[str] = None,
    url: Optional[str] = None,
    raw_input: Optional[str] = None,
    sync_key: Optional[str] = None,
    api_key: Optional[str] = None,
    vault_path: Optional[str] = None,
) -> dict[str, Any]:
    """Save or update a place in the local E2EE Vault and sync to Cloud Relay."""
    norm_status = (status or "wishlist").strip().lower()
    if norm_status not in VALID_VAULT_STATUSES:
        raise ValueError(f"Invalid vault status '{status}'. Must be one of: {', '.join(VALID_VAULT_STATUSES)}")

    # Auto-lookup convenience if raw_input / URL is provided
    target_str = (raw_input or url or "").strip()
    if target_str and (not place_id or not name):
        extracted_url = extract_google_maps_url(target_str)
        if extracted_url:
            url = url or extracted_url
            resolved = resolve_short_url(extracted_url)
            extracted_id = extract_maps_data_id(resolved)
            if extracted_id and not place_id:
                place_id = extracted_id
            coords = extract_coordinates(resolved)
            if coords and (lat is None or lng is None):
                lat, lng = coords
        if place_id:
            cached = _cache_load(_cache_path(place_id))
            if cached and isinstance(cached.get("place"), dict):
                cp = cached["place"]
                name = name or cp.get("name")
                address = address or cp.get("address")
                if google_rating is None and cp.get("average_rating") is not None:
                    google_rating = float(cp["average_rating"])
        if not name and not extracted_url:
            name = target_str

    if not name and not place_id:
        raise ValueError("vault_save_place requires at least --name, --place-id, or a place URL/query.")

    if not place_id:
        norm_name = _normalize_for_match(name or "place")
        coord_part = f"{lat:.4f},{lng:.4f}" if (lat is not None and lng is not None) else ""
        digest = hashlib.sha256(f"{norm_name}:{coord_part}".encode("utf-8")).hexdigest()[:16]
        place_id = f"custom_{digest}"

    now_iso = _now_iso_utc()
    vault = load_local_vault(vault_path)
    places = vault.setdefault("places", {})

    existing = places.get(place_id)
    # Also check if an existing place matches by normalized name when custom place_id was generated
    if existing is None and place_id.startswith("custom_") and name:
        for pid, prec in places.items():
            if _normalize_for_match(str(prec.get("name") or "")) == _normalize_for_match(name):
                place_id = pid
                existing = prec
                break

    incoming_rec: dict[str, Any] = {
        "place_id": place_id,
        "name": name or (existing or {}).get("name") or place_id,
        "address": address,
        "lat": float(lat) if lat is not None else None,
        "lng": float(lng) if lng is not None else None,
        "status": norm_status,
        "user_rating": float(user_rating) if user_rating is not None else None,
        "google_rating": float(google_rating) if google_rating is not None else None,
        "tags": _split_csv_items(tags),
        "must_try": _split_csv_items(must_try),
        "red_flags": _split_csv_items(red_flags),
        "insider_tips": _split_csv_items(insider_tips),
        "user_notes": (user_notes or "").strip(),
        "url": url,
        "saved_at": now_iso,
        "visited_at": now_iso if norm_status in ("liked", "disliked") else None,
    }

    if existing:
        merged_rec = _merge_place_records(existing, incoming_rec)
    else:
        merged_rec = incoming_rec

    places[place_id] = merged_rec

    resolved_key, is_new_key = resolve_sync_key(sync_key, auto_create=True)
    global _LAST_VAULT_SYNC_DIAG
    _LAST_VAULT_SYNC_DIAG = {}
    cloud_synced = False
    if resolved_key:
        cloud_synced = _cloud_vault_put(vault, resolved_key)
    vault["sync_pending"] = not cloud_synced
    saved_file = save_local_vault(vault, vault_path)
    obsidian_file = export_obsidian_markdown(merged_rec)

    res: dict[str, Any] = {
        "status": "success",
        "mode": "vault_save",
        "action": "updated" if existing else "created",
        "place": merged_rec,
        "total_saved_places": len(places),
        "vault_path": saved_file,
        "cloud_synced": cloud_synced,
        "sync_pending": vault["sync_pending"],
        "new_sync_key_generated": is_new_key,
    }
    if not cloud_synced:
        diag = _LAST_VAULT_SYNC_DIAG or {
            "error_type": "unknown_error",
            "message": "Cloud Vault sync did not complete (saved locally).",
            "hint": "請執行 --vault-status 檢查連線狀態，或稍後執行 --vault-sync 重試。",
        }
        res["cloud_sync_error_type"] = diag.get("error_type")
        res["cloud_sync_error"] = diag.get("message")
        res["cloud_sync_hint"] = diag.get("hint")
    if is_new_key and resolved_key:
        res["sync_key"] = resolved_key
    if obsidian_file:
        res["obsidian_path"] = obsidian_file
    return res


def vault_query_places(
    status: str = "all",
    keyword: Optional[str] = None,
    tag: Optional[str] = None,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    radius_km: Optional[float] = None,
    limit: int = 10,
    sync_key: Optional[str] = None,
    vault_path: Optional[str] = None,
) -> dict[str, Any]:
    """Query saved places in the Vault with status, keyword, tag, and Haversine radius filtering (0 SerpApi credits)."""
    vault = load_local_vault(vault_path)
    resolved_key, _ = resolve_sync_key(sync_key, auto_create=False)

    # Hydrate from cloud if local vault has no places yet and a sync key is available
    if not vault.get("places") and resolved_key:
        remote_vault = _cloud_vault_get(resolved_key)
        if remote_vault and isinstance(remote_vault.get("places"), dict):
            vault["places"] = remote_vault["places"]
            vault["sync_pending"] = False
            save_local_vault(vault, vault_path)

    places_map: dict[str, dict[str, Any]] = vault.get("places") or {}
    norm_status = (status or "all").strip().lower()
    kw_norm = (keyword or "").strip().casefold()
    tag_norm = (tag or "").strip().casefold()
    has_coords = lat is not None and lng is not None
    effective_radius_km = float(radius_km) if radius_km is not None else (5.0 if has_coords else None)

    matched: list[dict[str, Any]] = []
    for pid, rec in places_map.items():
        if not isinstance(rec, dict):
            continue
        rec_status = str(rec.get("status") or "wishlist").lower()
        if norm_status not in ("all", "") and rec_status != norm_status:
            continue

        if tag_norm:
            rec_tags = [str(t).casefold() for t in (rec.get("tags") or [])]
            if not any(tag_norm in rt for rt in rec_tags):
                continue

        if kw_norm:
            searchable_blob = " ".join([
                str(rec.get("name") or ""),
                str(rec.get("address") or ""),
                " ".join(str(x) for x in (rec.get("tags") or [])),
                " ".join(str(x) for x in (rec.get("must_try") or [])),
                " ".join(str(x) for x in (rec.get("red_flags") or [])),
                " ".join(str(x) for x in (rec.get("insider_tips") or [])),
                str(rec.get("user_notes") or ""),
            ]).casefold()
            if kw_norm not in searchable_blob:
                continue

        item = dict(rec)
        if has_coords and lat is not None and lng is not None:
            p_lat = item.get("lat")
            p_lng = item.get("lng")
            if p_lat is None or p_lng is None:
                continue
            dist_m = haversine_distance_m(lat, lng, float(p_lat), float(p_lng))
            dist_km = round(dist_m / 1000.0, 3)
            if effective_radius_km is not None and dist_km > effective_radius_km:
                continue
            item["distance_km"] = dist_km
            item["distance_m"] = dist_m

        matched.append(item)

    if has_coords:
        matched.sort(key=lambda x: (x.get("distance_km", 10**9), str(x.get("name") or "")))
    else:
        matched.sort(key=lambda x: str(x.get("saved_at") or ""), reverse=True)

    max_items = max(1, int(limit or 10))
    return {
        "status": "success",
        "mode": "vault_query",
        "filters": {
            "status": norm_status,
            "keyword": keyword,
            "tag": tag,
            "lat": lat,
            "lng": lng,
            "radius_km": effective_radius_km,
        },
        "total_in_vault": len(places_map),
        "matched_count": len(matched),
        "places": matched[:max_items],
    }


def vault_delete_place(
    place_id: Optional[str] = None,
    name: Optional[str] = None,
    sync_key: Optional[str] = None,
    vault_path: Optional[str] = None,
) -> dict[str, Any]:
    """Delete a saved place from the Vault by place_id or exact/fuzzy name match."""
    if not place_id and not name:
        raise ValueError("vault_delete_place requires --place-id or --name.")

    vault = load_local_vault(vault_path)
    places: dict[str, dict[str, Any]] = vault.get("places") or {}
    removed: list[dict[str, Any]] = []

    if place_id and place_id in places:
        removed.append(places.pop(place_id))
    elif name:
        target_norm = _normalize_for_match(name)
        target_cf = name.strip().casefold()
        to_delete = [
            pid for pid, rec in places.items()
            if (
                str(rec.get("name") or "").strip().casefold() == target_cf
                or _normalize_for_match(str(rec.get("name") or "")) == target_norm
                or _query_matches_title(name, str(rec.get("name") or ""))
            )
        ]
        for pid in to_delete:
            removed.append(places.pop(pid))

    global _LAST_VAULT_SYNC_DIAG
    _LAST_VAULT_SYNC_DIAG = {}
    cloud_synced = False
    if removed:
        resolved_key, _ = resolve_sync_key(sync_key, auto_create=False)
        if resolved_key:
            cloud_synced = _cloud_vault_put(vault, resolved_key)
        vault["sync_pending"] = not cloud_synced
        save_local_vault(vault, vault_path)

    res: dict[str, Any] = {
        "status": "success",
        "mode": "vault_delete",
        "deleted_count": len(removed),
        "deleted": removed,
        "remaining_count": len(places),
        "cloud_synced": cloud_synced,
    }
    if removed and not cloud_synced and _LAST_VAULT_SYNC_DIAG:
        res["cloud_sync_error_type"] = _LAST_VAULT_SYNC_DIAG.get("error_type")
        res["cloud_sync_error"] = _LAST_VAULT_SYNC_DIAG.get("message")
        res["cloud_sync_hint"] = _LAST_VAULT_SYNC_DIAG.get("hint")
    return res


def vault_sync(
    sync_key: Optional[str] = None,
    vault_path: Optional[str] = None,
) -> dict[str, Any]:
    """Pull remote encrypted vault, decrypt, merge with local items (most recent saved_at wins), and push back."""
    resolved_key, is_new_key = resolve_sync_key(sync_key, auto_create=True)
    if not resolved_key:
        raise VaultError("Unable to resolve or generate TLDREVIEW_SYNC_KEY.")

    global _LAST_VAULT_SYNC_DIAG
    _LAST_VAULT_SYNC_DIAG = {}

    local_vault = load_local_vault(vault_path)
    local_places: dict[str, dict[str, Any]] = local_vault.get("places") or {}

    remote_vault = _cloud_vault_get(resolved_key)
    remote_places: dict[str, dict[str, Any]] = (
        remote_vault.get("places") if isinstance(remote_vault, dict) and isinstance(remote_vault.get("places"), dict) else {}
    )

    merged_places: dict[str, dict[str, Any]] = {}
    all_pids = set(local_places.keys()) | set(remote_places.keys())
    for pid in all_pids:
        l_rec = local_places.get(pid)
        r_rec = remote_places.get(pid)
        if l_rec and r_rec:
            l_time = str(l_rec.get("saved_at") or "")
            r_time = str(r_rec.get("saved_at") or "")
            if r_time > l_time:
                merged_places[pid] = _merge_place_records(l_rec, r_rec)
            else:
                merged_places[pid] = _merge_place_records(r_rec, l_rec)
        elif l_rec:
            merged_places[pid] = l_rec
        elif r_rec:
            merged_places[pid] = r_rec

    local_vault["places"] = merged_places
    cloud_synced = _cloud_vault_put(local_vault, resolved_key)
    local_vault["sync_pending"] = not cloud_synced
    saved_file = save_local_vault(local_vault, vault_path)

    res: dict[str, Any] = {
        "status": "success",
        "mode": "vault_sync",
        "total_places": len(merged_places),
        "pulled_remote_places": len(remote_places),
        "cloud_synced": cloud_synced,
        "sync_pending": local_vault["sync_pending"],
        "vault_path": saved_file,
        "new_sync_key_generated": is_new_key,
    }
    if not cloud_synced:
        diag = _LAST_VAULT_SYNC_DIAG or {
            "error_type": "unknown_error",
            "message": "Cloud Vault sync did not complete (saved locally).",
            "hint": "請執行 --vault-status 檢查連線狀態，或稍後執行 --vault-sync 重試。",
        }
        res["cloud_sync_error_type"] = diag.get("error_type")
        res["cloud_sync_error"] = diag.get("message")
        res["cloud_sync_hint"] = diag.get("hint")
    if is_new_key:
        res["sync_key"] = resolved_key
    return res


def vault_status(
    sync_key: Optional[str] = None,
    vault_path: Optional[str] = None,
) -> dict[str, Any]:
    """Run 3-layer diagnostics on local Vault file, Sync Key configuration, and Cloud Relay connectivity."""
    path = vault_path or get_vault_path()
    local_exists = os.path.isfile(path)
    local_vault = load_local_vault(path)
    local_places: dict[str, dict[str, Any]] = local_vault.get("places") or {}
    status_counts = {"wishlist": 0, "liked": 0, "disliked": 0}
    for rec in local_places.values():
        if isinstance(rec, dict):
            st = str(rec.get("status") or "wishlist").lower()
            status_counts[st] = status_counts.get(st, 0) + 1

    # Resolve key source without auto-creating
    key_source = "not_configured"
    resolved_key: Optional[str] = None
    key_error: Optional[str] = None
    vault_id: Optional[str] = None
    try:
        if sync_key and sync_key.strip():
            resolved_key = sync_key.strip()
            vault_id, _ = parse_sync_key(resolved_key)
            key_source = "cli_arg"
        else:
            load_dotenv_if_present()
            env_key = os.getenv("TLDREVIEW_SYNC_KEY", "").strip()
            if env_key:
                resolved_key = env_key
                vault_id, _ = parse_sync_key(resolved_key)
                key_source = "env:TLDREVIEW_SYNC_KEY"
            else:
                kpath = get_sync_key_path()
                if os.path.isfile(kpath):
                    with open(kpath, "r", encoding="utf-8") as f:
                        saved = f.read().strip()
                    if saved:
                        resolved_key = saved
                        vault_id, _ = parse_sync_key(resolved_key)
                        key_source = f"file:{kpath}"
    except Exception as exc:
        key_error = str(exc)

    api_base = (os.getenv("TLDREVIEW_VAULT_API_URL") or VAULT_API_DEFAULT_BASE).rstrip("/")
    health_url = _vault_health_url()
    offline_flag = os.getenv("TLDREVIEW_VAULT_OFFLINE", "").strip().lower() in {"1", "true", "yes"}

    relay_health: dict[str, Any] = {
        "api_base_url": api_base,
        "health_url": health_url,
        "offline_mode": offline_flag,
        "reachable": False,
        "latency_ms": None,
    }
    if not offline_flag:
        t0 = time.monotonic()
        req = urllib.request.Request(
            health_url,
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=VAULT_HTTP_TIMEOUT) as resp:
                relay_health["reachable"] = resp.status == 200
                relay_health["http_status"] = resp.status
                relay_health["latency_ms"] = round((time.monotonic() - t0) * 1000)
        except Exception as exc:
            diag = _classify_vault_error(exc, url=health_url)
            relay_health["error_type"] = diag["error_type"]
            relay_health["error"] = diag["message"]
            relay_health["hint"] = diag["hint"]

    remote_info: dict[str, Any] = {
        "checked": False,
        "exists": False,
        "decryption_ok": None,
        "remote_places_count": None,
        "in_sync": False,
    }
    if resolved_key and vault_id and relay_health.get("reachable"):
        remote_info["checked"] = True
        target_url = _vault_api_url(vault_id)
        req = urllib.request.Request(
            target_url,
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=VAULT_HTTP_TIMEOUT) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                envelope = json.loads(raw)
                remote_info["exists"] = True
                remote_info["envelope_updated_at"] = envelope.get("updated_at")
                try:
                    decrypted = decrypt_vault_payload(envelope, resolved_key)
                    r_places = decrypted.get("places") or {}
                    remote_info["decryption_ok"] = True
                    remote_info["remote_places_count"] = len(r_places)
                    remote_info["remote_updated_at"] = decrypted.get("updated_at")
                    remote_info["in_sync"] = (
                        not local_vault.get("sync_pending", False)
                        and set(local_places.keys()) == set(r_places.keys())
                    )
                except VaultDecryptionError as dexc:
                    remote_info["decryption_ok"] = False
                    remote_info["error_type"] = "decryption_error"
                    remote_info["error"] = str(dexc)
                    remote_info["hint"] = "雲端金庫存在但本地金鑰無法解密（HMAC 驗證失敗），請確認兩端 TLDREVIEW_SYNC_KEY 是否相同。"
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                remote_info["exists"] = False
                remote_info["remote_places_count"] = 0
                remote_info["in_sync"] = len(local_places) == 0 and not local_vault.get("sync_pending", False)
            else:
                diag = _classify_vault_error(exc, url=target_url)
                remote_info["error_type"] = diag["error_type"]
                remote_info["error"] = diag["message"]
                remote_info["hint"] = diag["hint"]
        except Exception as exc:
            diag = _classify_vault_error(exc, url=target_url)
            remote_info["error_type"] = diag["error_type"]
            remote_info["error"] = diag["message"]
            remote_info["hint"] = diag["hint"]

    return {
        "status": "success",
        "mode": "vault_status",
        "local": {
            "vault_path": path,
            "file_exists": local_exists,
            "total_places": len(local_places),
            "status_counts": status_counts,
            "sync_pending": bool(local_vault.get("sync_pending", False)),
            "updated_at": local_vault.get("updated_at"),
        },
        "sync_key": {
            "configured": bool(resolved_key and vault_id),
            "source": key_source,
            "vault_id": vault_id,
            "error": key_error,
        },
        "cloud_relay": relay_health,
        "remote_vault": remote_info,
    }


def format_vault_markdown_output(result: dict) -> str:
    """Format Vault save/query/delete/sync/status results into clean human & Agent-friendly Markdown."""
    mode = result.get("mode")
    lines: list[str] = []

    if mode == "vault_save":
        p = result.get("place") or {}
        action_zh = "已更新" if result.get("action") == "updated" else "已新增"
        status_badge = {
            "wishlist": "📌 想去清單 (wishlist)",
            "liked": "❤️ 喜歡/推薦 (liked)",
            "disliked": "💣 避雷黑名單 (disliked)",
        }.get(str(p.get("status") or "wishlist"), str(p.get("status")))
        lines.append(f"# 🔒 TL;DReview 個人美食金庫 ({action_zh})：{p.get('name')}")
        lines.append(f"- **狀態**: {status_badge} | **Place ID**: `{p.get('place_id')}`")
        if p.get("address"):
            lines.append(f"- **地址**: {p['address']}")
        if p.get("google_rating") is not None or p.get("user_rating") is not None:
            lines.append(
                f"- **評分**: Google ⭐ {p.get('google_rating', 'N/A')} | 個人評分 ⭐ {p.get('user_rating', 'N/A')}"
            )
        if p.get("tags"):
            lines.append(f"- **標籤 (Tags)**: {', '.join(p['tags'])}")
        if p.get("must_try"):
            lines.append(f"- **🍜 必點招牌**: {', '.join(p['must_try'])}")
        if p.get("red_flags"):
            lines.append(f"- **👎 避雷提醒**: {', '.join(p['red_flags'])}")
        if p.get("insider_tips"):
            lines.append(f"- **🤫 內行撇步**: {', '.join(p['insider_tips'])}")
        if p.get("user_notes"):
            lines.append(f"- **📝 個人筆記**: {p['user_notes']}")
        if result.get("cloud_synced"):
            lines.append(f"- **同步狀態**: ✅ 已 E2EE 端到端加密同步至雲端 (金庫共 {result.get('total_saved_places', 1)} 間店)")
        else:
            lines.append(
                f"- **同步狀態**: ⚠️ **僅存於本地快取，雲端同步未完成 (`sync_pending: true`)** (本地共 {result.get('total_saved_places', 1)} 間店)"
            )
            err_msg = result.get("cloud_sync_error") or "無法連線至雲端中繼站"
            err_type = result.get("cloud_sync_error_type") or "network_error"
            hint_msg = result.get("cloud_sync_hint") or "請執行 --vault-status 檢查連線狀態，或稍後執行 --vault-sync 補推上雲端。"
            lines.extend([
                "",
                "> [!WARNING]",
                f"> ⚠️ **雲端同步失敗 (`{err_type}`)**：{err_msg}",
                f"> 💡 **排錯建議**：{hint_msg}",
            ])
        if result.get("new_sync_key_generated") and result.get("sync_key"):
            lines.extend([
                "",
                "> [!IMPORTANT]",
                f"> 🔑 **首次自動產生 E2EE 跨裝置同步金鑰 (`TLDREVIEW_SYNC_KEY`)**：",
                f"> `{result['sync_key']}`",
                "> 請將此金鑰顯示給使用者並存入 AI Agent 記憶（Memory）或環境變數 `TLDREVIEW_SYNC_KEY`，未來在任何雲端沙盒皆可無縫同步個人清單！",
            ])
        return "\n".join(lines)

    if mode == "vault_query":
        places = result.get("places") or []
        lines.append(
            f"# 🗂️ TL;DReview 個人美食金庫查詢結果 (符合 {result.get('matched_count', 0)} / 總計 {result.get('total_in_vault', 0)} 間)"
        )
        if not places:
            lines.append("\n*(目前沒有符合篩選條件的已儲存店家)*")
            return "\n".join(lines)
        for idx, p in enumerate(places, 1):
            st = str(p.get("status") or "wishlist")
            icon = {"wishlist": "📌", "liked": "❤️", "disliked": "💣"}.get(st, "📌")
            dist_str = f" | 📍 距離 {p['distance_km']:.2f} km" if p.get("distance_km") is not None else ""
            g_str = f" | Google ⭐{p['google_rating']}" if p.get("google_rating") is not None else ""
            u_str = f" | 個人 ⭐{p['user_rating']}" if p.get("user_rating") is not None else ""
            lines.append(f"## [{idx}] {icon} {p.get('name')} (`{st}`{dist_str}{g_str}{u_str})")
            if p.get("address"):
                lines.append(f"- **地址**: {p['address']}")
            if p.get("tags"):
                lines.append(f"- **標籤**: {', '.join(p['tags'])}")
            if p.get("must_try"):
                lines.append(f"- **🍜 必點**: {', '.join(p['must_try'])}")
            if p.get("red_flags"):
                lines.append(f"- **👎 雷點**: {', '.join(p['red_flags'])}")
            if p.get("insider_tips"):
                lines.append(f"- **🤫 撇步**: {', '.join(p['insider_tips'])}")
            if p.get("user_notes"):
                lines.append(f"- **📝 筆記**: {p['user_notes']}")
        return "\n".join(lines)

    if mode == "vault_delete":
        lines.append(f"# 🗑️ TL;DReview 金庫刪除結果：已移除 {result.get('deleted_count', 0)} 間店家")
        for d in result.get("deleted") or []:
            lines.append(f"- 已移除：**{d.get('name')}** (`{d.get('place_id')}`)")
        if result.get("deleted_count", 0) > 0 and not result.get("cloud_synced"):
            lines.append(
                f"- ⚠️ **雲端尚未同步 (`{result.get('cloud_sync_error_type', 'offline')}`)**: {result.get('cloud_sync_error', '')}"
            )
        return "\n".join(lines)

    if mode == "vault_sync":
        if result.get("cloud_synced"):
            lines.append(
                f"# 🔄 TL;DReview E2EE 金庫同步完成：共 {result.get('total_places', 0)} 間店家 (✅ 雲端已同步)"
            )
        else:
            lines.append(
                f"# ⚠️ TL;DReview E2EE 金庫同步未完成：本地共 {result.get('total_places', 0)} 間店家 (⚠️ 僅存於本地快取)"
            )
            lines.extend([
                "",
                "> [!WARNING]",
                f"> ⚠️ **雲端同步失敗 (`{result.get('cloud_sync_error_type', 'network_error')}`)**: {result.get('cloud_sync_error', '')}",
                f"> 💡 **排錯建議**: {result.get('cloud_sync_hint', '')}",
            ])
        if result.get("new_sync_key_generated") and result.get("sync_key"):
            lines.append(f"\n🔑 新產生的同步金鑰 (`TLDREVIEW_SYNC_KEY`): `{result['sync_key']}`")
        return "\n".join(lines)

    if mode == "vault_status":
        loc = result.get("local") or {}
        sk = result.get("sync_key") or {}
        cr = result.get("cloud_relay") or {}
        rv = result.get("remote_vault") or {}
        counts = loc.get("status_counts") or {}
        lines.append("# 🩺 TL;DReview 個人美食金庫連線與狀態診斷 (`--vault-status`)")
        lines.append(
            f"1. **本地快取 (`{loc.get('vault_path')}`)**: "
            f"共 **{loc.get('total_places', 0)}** 間店 "
            f"(📌 想去 `{counts.get('wishlist', 0)}` / ❤️ 喜歡 `{counts.get('liked', 0)}` / 💣 避雷 `{counts.get('disliked', 0)}`) | "
            f"待同步 (`sync_pending`): `{loc.get('sync_pending')}`"
        )
        if sk.get("configured"):
            lines.append(f"2. **E2EE 同步金鑰**: ✅ 已設定 (來源: `{sk.get('source')}` | Vault ID: `{sk.get('vault_id')}`)")
        else:
            err_suffix = f" ({sk.get('error')})" if sk.get("error") else ""
            lines.append(f"2. **E2EE 同步金鑰**: ⚠️ 尚未設定{err_suffix}")
        if cr.get("reachable"):
            lines.append(f"3. **Cloudflare 雲端中繼站 (`{cr.get('api_base_url')}`)**: ✅ 連線正常 (`{cr.get('latency_ms')} ms`)")
        else:
            lines.append(
                f"3. **Cloudflare 雲端中繼站 (`{cr.get('api_base_url')}`)**: ❌ 無法連線 (`{cr.get('error_type', 'offline')}`: {cr.get('error', '')})"
            )
            if cr.get("hint"):
                lines.append(f"   - 💡 **排錯建議**: {cr['hint']}")
        if rv.get("checked"):
            if rv.get("exists") and rv.get("decryption_ok"):
                sync_badge = "✅ 本地與雲端完全一致" if rv.get("in_sync") else "⚠️ 本地與雲端有差異（請執行 `--vault-sync`）"
                lines.append(
                    f"4. **雲端加密金庫驗證**: ✅ 雲端共有 **{rv.get('remote_places_count', 0)}** 間店 (E2EE 解密驗證通過) | {sync_badge}"
                )
            elif not rv.get("exists"):
                lines.append("4. **雲端加密金庫驗證**: ℹ️ 雲端尚無此 Vault ID 的紀錄（請執行 `--vault-sync` 首次推送）")
            else:
                lines.append(f"4. **雲端加密金庫驗證**: ❌ 驗證失敗 (`{rv.get('error_type')}`: {rv.get('error')})")
                if rv.get("hint"):
                    lines.append(f"   - 💡 **排錯建議**: {rv['hint']}")
        return "\n".join(lines)

    return json.dumps(result, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# CLI Entrypoint
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Extract and curate Google Maps reviews from a URL, store/keyword query "
            "(with optional coordinates), signboard/menu photo, multi-venue --compare, --top N discovery, "
            "or manage your E2EE Personal Dining Vault (--vault-save / --vault-query / --vault-status)."
        )
    )
    parser.add_argument(
        "urls",
        nargs="*",
        help="One or more Google Maps links, store names, addresses, or search queries",
    )
    parser.add_argument("--query", "-q", help="Explicit search query (store name, keyword, or address)")
    parser.add_argument("--lat", type=float, help="Latitude for nearby search or vault place/query (e.g. 25.0330)")
    parser.add_argument("--lng", "--lon", dest="lng", type=float, help="Longitude for nearby search or vault place/query (e.g. 121.5654)")
    parser.add_argument("--ll", help="Coordinates in '@lat,lng,15z' or 'lat,lng' format")
    parser.add_argument("--zoom", type=int, default=15, help="Map zoom level when searching with coordinates (default: 15)")
    parser.add_argument("--image", help="Path to a photo of a store signboard, menu, or business card")
    parser.add_argument(
        "--compare",
        action="store_true",
        help="Side-by-side comparison mode for 2+ venues with scaled token budgets",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=0,
        help="Area Discovery mode: scan top N organic places nearby, audit manipulation, and crown the true winner",
    )
    # E2EE Personal Dining Vault flags
    parser.add_argument("--vault-save", action="store_true", help="Save or update a place in your E2EE Personal Dining Vault")
    parser.add_argument("--vault-query", action="store_true", help="Query your E2EE Personal Dining Vault (0 SerpApi credits)")
    parser.add_argument("--vault-delete", action="store_true", help="Delete a place from your Personal Dining Vault")
    parser.add_argument("--vault-sync", action="store_true", help="Sync and merge local Vault with the E2EE Cloud Relay")
    parser.add_argument("--vault-status", action="store_true", help="Run diagnostics on local Vault, Sync Key, and Cloud Relay connectivity")
    parser.add_argument("--sync-key", help="E2EE Sync Key (tld_<vault_id>_<master_secret>) or set TLDREVIEW_SYNC_KEY")
    parser.add_argument("--place-id", help="Google Maps data_id / place_id for Vault operations")
    parser.add_argument("--name", help="Store name for Vault save or delete")
    parser.add_argument("--address", help="Store address for Vault save")
    parser.add_argument("--google-rating", type=float, help="Google Maps average rating for Vault save")
    parser.add_argument(
        "--status",
        default="wishlist",
        help="Vault status: wishlist (default), liked, disliked, or all (for --vault-query)",
    )
    parser.add_argument("--user-rating", type=float, help="Personal rating (1-5) for Vault save")
    parser.add_argument("--tags", help="Comma-separated tags for Vault save (e.g. '牛肉麵,大安區')")
    parser.add_argument("--tag", help="Tag filter for --vault-query")
    parser.add_argument("--keyword", help="Keyword filter for --vault-query")
    parser.add_argument("--must-try", help="Comma-separated must-try dishes for Vault save")
    parser.add_argument("--red-flags", help="Comma-separated red flags / caveats for Vault save")
    parser.add_argument("--insider-tips", help="Comma-separated insider tips for Vault save")
    parser.add_argument("--user-notes", help="Personal notes for Vault save")
    parser.add_argument("--radius-km", type=float, help="Search radius in km for --vault-query with --lat/--lng (default: 5.0)")
    parser.add_argument("--limit", type=int, default=10, help="Max results to return for --vault-query (default: 10)")

    parser.add_argument("--api-key", help="SerpApi API Key (or set SERPAPI_API_KEY in environment / .env)")
    parser.add_argument("--lang", default="zh-TW", help="Preferred summary language code (default: zh-TW)")
    parser.add_argument(
        "--sort-by",
        default="qualityScore",
        help="Review sort order: qualityScore (relevant), newest (newestFirst), lowest (ratingLow), highest (ratingHigh)",
    )
    parser.add_argument(
        "--save-quota",
        action="store_true",
        help="Minimize SerpApi credit usage by skipping extra detail/pagination/English refetch calls",
    )
    parser.add_argument("--markdown", action="store_true", help="Output high-density human/LLM-readable Markdown")
    parser.add_argument("--no-cache", action="store_true", help="Ignore the 24h review cache and force fresh SerpApi calls")
    parser.add_argument("--parse-only", action="store_true", help="Only parse URL, query, or EXIF GPS without calling SerpApi")

    args, unknown_args = parser.parse_known_args()
    targets: list[str] = []
    if args.query:
        targets.append(args.query)
    if args.urls:
        targets.extend(u for u in args.urls if u and u.strip())
    if unknown_args:
        targets.extend(u for u in unknown_args if u and u.strip())
    raw_target = targets[0] if targets else ""

    lat, lng = args.lat, args.lng
    if args.ll and (lat is None or lng is None):
        parsed_ll = parse_ll_string(args.ll)
        if parsed_ll:
            lat, lng = parsed_ll[0], parsed_ll[1]

    # Handle Vault operations first (no SerpApi key required for local/vault operations!)
    if args.vault_save or args.vault_query or args.vault_delete or args.vault_sync or args.vault_status:
        try:
            if args.vault_save:
                # If status was not explicitly passed, default to wishlist
                v_res = vault_save_place(
                    place_id=args.place_id,
                    name=args.name,
                    address=args.address,
                    lat=lat,
                    lng=lng,
                    google_rating=args.google_rating,
                    status=args.status if args.status != "all" else "wishlist",
                    user_rating=args.user_rating,
                    tags=args.tags,
                    must_try=args.must_try,
                    red_flags=args.red_flags,
                    insider_tips=args.insider_tips,
                    user_notes=args.user_notes,
                    raw_input=raw_target,
                    sync_key=args.sync_key,
                    api_key=resolve_api_key(args.api_key),
                )
            elif args.vault_query:
                # If user didn't explicitly pass --status on CLI, query all statuses
                query_status = args.status if "--status" in sys.argv else "all"
                v_res = vault_query_places(
                    status=query_status,
                    keyword=args.keyword or (raw_target if raw_target else None),
                    tag=args.tag or args.tags,
                    lat=lat,
                    lng=lng,
                    radius_km=args.radius_km,
                    limit=args.limit,
                    sync_key=args.sync_key,
                )
            elif args.vault_delete:
                v_res = vault_delete_place(
                    place_id=args.place_id,
                    name=args.name or (raw_target if raw_target else None),
                    sync_key=args.sync_key,
                )
            elif args.vault_status:
                v_res = vault_status(sync_key=args.sync_key)
            else:
                v_res = vault_sync(sync_key=args.sync_key)
        except Exception as exc:
            print(json.dumps({"status": "error", "message": str(exc)}), file=sys.stdout)
            log(f"Vault operation failed: {exc}")
            return 1

        if args.markdown:
            print(format_vault_markdown_output(v_res))
        else:
            print(json.dumps(v_res, indent=2, ensure_ascii=False), file=sys.stdout)
        return 0

    # Parse only mode
    if args.parse_only:
        raw_url = extract_google_maps_url(raw_target)
        exif_coords = extract_exif_gps(args.image) if args.image else None
        if raw_url:
            resolved = resolve_short_url(raw_url)
            data_id = extract_maps_data_id(resolved)
            coords = extract_coordinates(resolved) or exif_coords
            print(json.dumps({
                "mode": "url",
                "original_url": raw_url,
                "resolved_url": resolved,
                "data_id": data_id,
                "coordinates": coords,
            }, indent=2, ensure_ascii=False), file=sys.stdout)
            return 0

        if (lat is None or lng is None) and exif_coords:
            lat, lng = exif_coords
        cleaned_text, inline_coords = extract_inline_coordinates(raw_target)
        if (lat is None or lng is None) and inline_coords:
            lat, lng = inline_coords
        cleaned_q = clean_search_query(cleaned_text, has_coords=(lat is not None and lng is not None))
        if not cleaned_q and not exif_coords:
            print(json.dumps({"error": "No Google Maps URL, search query, or image found in input"}), file=sys.stdout)
            return 1
        print(json.dumps({
            "mode": "search",
            "raw_query": raw_target,
            "cleaned_query": cleaned_q,
            "coordinates": [lat, lng] if (lat is not None and lng is not None) else None,
            "image": args.image,
        }, indent=2, ensure_ascii=False), file=sys.stdout)
        return 0

    api_key = resolve_api_key(args.api_key)
    if not api_key:
        print(
            "Error: SerpApi API Key is required. Provide via --api-key, SERPAPI_API_KEY env var, or .env file.",
            file=sys.stderr
        )
        return 1

    try:
        if args.compare or len(targets) >= 2:
            result = run_compare_pipeline(
                targets=targets,
                api_key=api_key,
                preferred_lang=args.lang,
                use_cache=not args.no_cache,
                sort_by=args.sort_by,
                save_quota=args.save_quota,
                lat=args.lat,
                lng=args.lng,
                ll=args.ll,
                zoom=args.zoom,
            )
        elif args.top and args.top > 0:
            result = run_top_discovery_pipeline(
                query=raw_target,
                api_key=api_key,
                top_n=args.top,
                preferred_lang=args.lang,
                use_cache=not args.no_cache,
                sort_by=args.sort_by,
                save_quota=args.save_quota,
                lat=args.lat,
                lng=args.lng,
                ll=args.ll,
                zoom=args.zoom,
            )
        else:
            result = run_pipeline(
                raw_target,
                api_key,
                preferred_lang=args.lang,
                use_cache=not args.no_cache,
                sort_by=args.sort_by,
                save_quota=args.save_quota,
                lat=args.lat,
                lng=args.lng,
                ll=args.ll,
                zoom=args.zoom,
                image_path=args.image,
            )
    except Exception as exc:
        safe_err = _redact_api_key(str(exc), api_key)
        print(json.dumps({"status": "error", "message": safe_err}), file=sys.stdout)
        log(f"Execution failed: {safe_err}")
        return 1

    if result.get("status") == "no_reviews":
        print(json.dumps(result, indent=2, ensure_ascii=False), file=sys.stdout)
        return 2

    if args.markdown:
        print(format_markdown_output(result, include_agent_instructions=False))
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False), file=sys.stdout)

    return 0


if __name__ == "__main__":
    sys.exit(main())
