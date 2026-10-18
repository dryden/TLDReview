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
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import json
import math
import mimetypes
import os
import re
import struct
import sys
import time
from typing import Any, Iterable, List, Optional, Tuple
import urllib.error
import urllib.parse
import urllib.request

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


def _query_matches_title(query: str, title: Optional[str]) -> bool:
    """Check if the place title matches the core store name in the query."""
    if not query or not title:
        return False
    q_norm = re.sub(r"[\s\-_・店分號]+", "", query.casefold())
    t_norm = re.sub(r"[\s\-_・店分號]+", "", title.casefold())
    if not q_norm or not t_norm:
        return False
    if q_norm in t_norm or t_norm in q_norm:
        return True
    # Also check first token of query (e.g. "全家便利商店" -> "全家")
    first_tok = re.split(r"\s+", query.strip())[0].casefold()
    for suffix in ("便利商店", "超商", "超市", "餐廳", "咖啡", "食堂"):
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


def clip_reviews_for_prompt(reviews: list[Review]) -> list[Tuple[Review, str]]:
    """Strictly clip reviews to prevent prompt token bloat."""
    clipped: list[Tuple[Review, str]] = []
    total_chars = 0

    for r in reviews:
        if len(clipped) >= MAX_REVIEWS_FOR_LLM or total_chars >= MAX_TOTAL_REVIEW_CHARS:
            break

        text = r.text.strip()
        cut = False

        # Single review length limit
        if len(text) > MAX_SINGLE_REVIEW_CHARS:
            text = _smart_truncate(text, MAX_SINGLE_REVIEW_CHARS)
            cut = True

        # Total review budget limit
        remaining = MAX_TOTAL_REVIEW_CHARS - total_chars
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


def format_markdown_output(result: dict, include_agent_instructions: bool = False) -> str:
    """Format pipeline result into a structured Markdown digest for CLI, MCP, and LangChain."""
    p = result.get("place") or {}
    s = result.get("stats") or {}
    search_meta = result.get("search") or {}
    target_lang = str(s.get("preferred_summary_language") or "en")
    is_zh = target_lang.lower().startswith("zh")

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
    if is_zh:
        lines.append(f"🏷️ 分類: {p.get('type') or 'unknown'} | {rating_str} (總評數: {p.get('total_reviews') or 0})")
    else:
        lines.append(f"🏷️ Category: {p.get('type') or 'unknown'} | {rating_str} (Total reviews: {p.get('total_reviews') or 0})")

    rs = p.get("rating_summary") or {}
    if rs:
        dist_parts = [f"{star}★({rs[str(star)]})" for star in (5, 4, 3, 2, 1) if str(star) in rs]
        if dist_parts:
            label = "星級分布" if is_zh else "Rating Distribution"
            lines.append(f"📈 {label}: {' | '.join(dist_parts)}")

    sort_label = s.get("sort_by") or "qualityScore"
    if is_zh:
        lines.append(
            f"📊 評論抽樣: 採集 {s.get('used_reviews') or 0} 則關鍵評論 "
            f"(來源語系: {s.get('fetch_language') or 'en'} | 排序: {sort_label})\n"
        )
        lines.append("## 精選評論清單 (Evidence Data)")
    else:
        lines.append(
            f"📊 Review Sample: {s.get('used_reviews') or 0} curated reviews "
            f"(Source language: {s.get('fetch_language') or 'en'} | Sort: {sort_label})\n"
        )
        lines.append("## Curated Reviews (Evidence Data)")
    lines.append(result.get("formatted_reviews") or "")

    if include_agent_instructions:
        if is_zh:
            lines.extend([
                "",
                "---",
                f"## 🦊 TL;DReview 分析與排版規範 (請以 `{target_lang}` 輸出)",
                "請嚴格根據上方精選評論證據（Evidence Data）產出結構化報告：",
                "1. **嚴禁幻覺（Anti-Hallucination）**：所有優缺點、招牌菜色、消費規則必須 100% 來自上述評論，未提及的維度請直接整段省略。",
                "2. **評分操縱示警**：若評論提及「五星好評送飲料/小菜/折價」等刷評活動，或星級呈極端 M 型分布，請務必於「💡 營運洞察與消費規則」明確示警。",
                "3. **兩極評價標註**：若同一項目評價分歧，請標註「（評價兩極）」並平實呈現雙方觀點。",
                "4. **7 大標準維度**：🍜 必點招牌 / 核心亮點、👍 優勢與推薦點、👎 缺點與避雷提醒、💡 營運洞察與消費規則、🤫 內行人的秘密撇步、🚗 交通與停車指南、🚨 特別點名。",
            ])
        else:
            lines.extend([
                "",
                "---",
                f"## 🦊 TL;DReview Analysis & Formatting Guidelines (Output in `{target_lang}`)",
                "Synthesize a structured intelligence briefing strictly grounded in the Evidence Data above:",
                "1. **Anti-Hallucination**: Every dish, pro, con, or rule MUST be 100% backed by the reviews above. Omit any section that has no direct evidence.",
                "2. **Rating Manipulation Radar**: If reviews mention '5-star review/check-in for freebies/discounts' or show an extreme bimodal rating distribution, explicitly warn the user under '💡 Operational Insights & Rules'.",
                "3. **Conflicting Feedback**: When opinions diverge sharply on an item, label it `(Mixed feedback)` and present both sides objectively.",
                "4. **7 Standard Sections**: 🍜 Must-Try / Core Highlights, 👍 Strengths & Pros, 👎 Weaknesses & Red Flags, 💡 Operational Insights & Rules, 🤫 Insider Tips, 🚗 Transit & Parking, 🚨 Notable Callouts.",
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
    if not text_input:
        raise ValueError("No Google Maps URL, store name, or search query provided.")

    # 1. Check for Google Maps URL vs Keyword / Nearby Search
    url = extract_google_maps_url(text_input)
    resolved_url: Optional[str] = None
    data_id: Optional[str] = None
    searched_place_info: Optional[PlaceInfo] = None
    search_meta: Optional[dict[str, Any]] = None
    fallback_query: Optional[str] = None

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

    cache_file = _cache_path(data_id, sort_by=canonical_sort)
    if use_cache:
        cached = _cache_load(cache_file) or _cache_load(
            _cache_path(data_id, preferred_lang, sort_by=canonical_sort)
        )
        if cached:
            log("Cache hit (< 24h) - skipping SerpApi review calls")
            cached["cached"] = True
            if isinstance(cached.get("stats"), dict):
                cached["stats"]["preferred_summary_language"] = preferred_lang
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
        p_info = raw_payload["place_info"]
        if not place_info.name:
            place_info.name = p_info.get("title") or place_info.name
        if not place_info.address:
            place_info.address = p_info.get("address") or place_info.address
        if not place_info.types and p_info.get("type"):
            p_type = p_info.get("type")
            place_info.types = [p_type] if isinstance(p_type, str) else list(p_type)
        if place_info.average_rating is None and p_info.get("rating") is not None:
            try:
                place_info.average_rating = float(p_info["rating"])
            except (ValueError, TypeError):
                pass

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
    clipped = clip_reviews_for_prompt(curated)

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
        },
        "stats": {
            "fetched_reviews": len(reviews),
            "curated_reviews": len(curated),
            "used_reviews": len(clipped),
            "fetch_language": fetch_lang,
            "preferred_summary_language": preferred_lang,
            "sort_by": canonical_sort,
            "save_quota": save_quota,
        },
        "formatted_reviews": format_reviews_block(clipped),
        "reviews": [
            {
                "author": r.author,
                "rating": r.rating,
                "relative_time": r.relative_time,
                "is_local_guide": r.is_local_guide,
                "like_count": r.like_count,
                "text": text,
            }
            for r, text in clipped
        ],
    }
    if search_meta:
        result["search"] = search_meta
    _cache_save(cache_file, result)
    return result


# ---------------------------------------------------------------------------
# CLI Entrypoint
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Extract and curate Google Maps reviews from a URL, store/keyword query "
            "(with optional coordinates), or signboard/menu photo."
        )
    )
    parser.add_argument(
        "url",
        nargs="?",
        default="",
        help="Google Maps link, store name, address, or search query (e.g. '附近的全家便利商店')",
    )
    parser.add_argument("--query", "-q", help="Explicit search query (store name, keyword, or address)")
    parser.add_argument("--lat", type=float, help="User current latitude for nearby search (e.g. 25.0330)")
    parser.add_argument("--lng", "--lon", dest="lng", type=float, help="User current longitude for nearby search (e.g. 121.5654)")
    parser.add_argument("--ll", help="Coordinates in '@lat,lng,15z' or 'lat,lng' format")
    parser.add_argument("--zoom", type=int, default=15, help="Map zoom level when searching with coordinates (default: 15)")
    parser.add_argument("--image", help="Path to a photo of a store signboard, menu, or business card")
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

    args = parser.parse_args()
    raw_target = (args.query if args.query is not None else args.url) or ""

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

        lat, lng = args.lat, args.lng
        if args.ll and (lat is None or lng is None):
            parsed_ll = parse_ll_string(args.ll)
            if parsed_ll:
                lat, lng = parsed_ll[0], parsed_ll[1]
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
