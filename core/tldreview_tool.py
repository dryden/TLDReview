#!/usr/bin/env python3
"""TL;DReview - Google Maps Review Extraction & Curation Tool.

Official Website: https://tldreview.app/

A zero-dependency standalone tool designed for agent sandboxes, including
Meta Muse and Grok Bot cloud computers.
Extracts Google Maps URLs, resolves redirects, grabs reviews via SerpApi,
filters and trims reviews for maximum density, and outputs structured data
for an LLM to analyze.

Zero third-party dependencies: uses only Python 3 standard library.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import json
import os
import re
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

# Regular Expressions for URL & ID Extraction
_URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_CID_RE = re.compile(r"0x[0-9a-f]+:0x[0-9a-f]+", re.IGNORECASE)
_PLACE_ID_RE = re.compile(r"ChI[0-9A-Za-z_-]+")
_HEX_ID_RE = re.compile(r"[0-9a-f]{16,}", re.IGNORECASE)
_COORD_BANG_RE = re.compile(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)")
_COORD_AT_RE = re.compile(r"@(-?\d+\.\d+),(-?\d+\.\d+)")
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


def log(msg: str) -> None:
    """Print progress logs to stderr to avoid polluting stdout."""
    print(f"[tldreview] {msg}", file=sys.stderr)


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
def extract_google_maps_url(text: str) -> Optional[str]:
    """Extract a Google Maps URL from free-form text or link wrappers."""
    for match in _URL_RE.finditer(text):
        candidate = match.group(0).rstrip(").,;'\"")
        # Handle Instagram redirect wrapper: l.instagram.com/?u=...
        host = urllib.parse.urlparse(candidate).netloc.lower()
        if host.endswith("instagram.com"):
            query_params = urllib.parse.parse_qs(urllib.parse.urlparse(candidate).query)
            for target in query_params.get("u", []):
                decoded = urllib.parse.unquote(target).strip()
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


def resolve_short_url(url: str) -> str:
    """Follow HTTP redirect chains for short links and unwrap Google consent pages."""
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    if not any(h in host for h in ("goo.gl", "share.google")):
        return url

    log(f"Resolving short URL: {url}")
    current_url = url
    try:
        # Custom redirect handler that doesn't lose location header
        req = urllib.request.Request(current_url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            current_url = resp.geturl()
    except urllib.error.HTTPError as exc:
        current_url = exc.geturl() or current_url
    except Exception as exc:
        log(f"URL resolve warning: {exc}")

    # Handle EU consent redirect: consent.google.com/...?continue=<target>
    parsed_final = urllib.parse.urlparse(current_url)
    if parsed_final.netloc.startswith("consent."):
        cont = urllib.parse.parse_qs(parsed_final.query).get("continue")
        if cont:
            current_url = cont[0]

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


def extract_coordinates(url: str) -> Optional[Tuple[float, float]]:
    """Extract latitude and longitude from URL pin or viewport."""
    for rx in (_COORD_BANG_RE, _COORD_AT_RE):
        m = rx.search(url)
        if m:
            return float(m.group(1)), float(m.group(2))
    return None


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
def http_get_json(url: str, params: dict[str, Any]) -> dict:
    """Perform HTTP GET request and return JSON object."""
    qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    full_url = f"{url}?{qs}"
    req = urllib.request.Request(
        full_url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            data = resp.read().decode("utf-8", "replace")
            return json.loads(data)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"SerpApi HTTP {exc.code}: {body}") from exc
    except Exception as exc:
        raise RuntimeError(f"Network request to SerpApi failed: {exc}") from exc


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
    if not isinstance(core, dict):
        core = {}

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
            )
        )
    return reviews


def fetch_serpapi_reviews(
    api_key: str,
    data_id: str,
    lang: str = "en",
    page_token: Optional[str] = None
) -> Tuple[list[Review], Optional[int], Optional[str], dict]:
    """Fetch reviews from SerpApi google_maps_reviews engine."""
    lookup = reviews_lookup_params(data_id)
    params: dict[str, Any] = {
        "api_key": api_key,
        "engine": "google_maps_reviews",
        "hl": lang,
        "sort_by": "qualityScore",  # SerpApi value for "most relevant"
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
def curate_reviews(reviews: list[Review]) -> list[Review]:
    """Curate reviews by prioritizing recent Local Guides, high likes, and pad to 10."""
    if not reviews:
        return []

    cutoff = datetime.now(timezone.utc) - RECENT_MAX_AGE
    guides: list[Review] = []
    liked: list[Review] = []
    others: list[Review] = []

    for r in reviews:
        dt = r.published_dt()
        # Keep if within 2 years or if date is missing (can't determine age)
        if dt and dt < cutoff:
            continue

        if r.is_local_guide:
            guides.append(r)
        elif (r.like_count or 0) > 1:
            liked.append(r)
        else:
            others.append(r)

    selected = guides + liked + others

    # If < 10, pad with remaining reviews to ensure sufficient evidence
    if len(selected) < MIN_SELECTED_REVIEWS:
        selected_ids = {id(r) for r in selected}
        for r in reviews:
            if id(r) not in selected_ids:
                selected.append(r)
                if len(selected) >= MIN_SELECTED_REVIEWS:
                    break

    return selected or reviews


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
            text = text[:MAX_SINGLE_REVIEW_CHARS].rstrip()
            cut = True

        # Total review budget limit
        remaining = MAX_TOTAL_REVIEW_CHARS - total_chars
        if len(text) > remaining:
            if remaining <= 10:
                break
            text = text[: remaining - 1].rstrip()
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
        rating_str = f" ★{r.rating:.0f}" if r.rating else ""
        date_str = f" ({r.relative_time})" if r.relative_time else ""
        guide_str = " [Local Guide]" if r.is_local_guide else ""
        likes_str = f" [+{r.like_count} helpful]" if (r.like_count or 0) > 0 else ""
        lines.append(f"[{idx}] {r.author}{guide_str}{rating_str}{date_str}{likes_str}: {text}")
    return "\n".join(lines)


CACHE_TTL_SECONDS = 24 * 60 * 60


def _cache_path(data_id: str, preferred_lang: str) -> str:
    base = os.getenv("TLDREVIEW_CACHE_DIR") or os.path.join(
        os.path.expanduser("~"), ".cache", "tldreview"
    )
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", f"{data_id}__{preferred_lang}")
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


def run_pipeline(
    raw_input: str,
    api_key: str,
    preferred_lang: str = "zh-TW",
    use_cache: bool = True
) -> dict:
    """Full execution pipeline."""
    # 1. Extract URL
    url = extract_google_maps_url(raw_input)
    if not url:
        raise ValueError(f"No valid Google Maps URL found in input: {raw_input}")

    # 2. Resolve redirects
    resolved_url = resolve_short_url(url)

    # 3. Extract data_id
    data_id = extract_maps_data_id(resolved_url)
    if not data_id:
        # Fallback: try coordinates or raise
        coords = extract_coordinates(resolved_url)
        if not coords:
            raise ValueError(f"Unable to extract data_id or coordinates from URL: {resolved_url}")
        raise ValueError(f"URL contains coordinates ({coords}) but no place data_id. Please use full share link.")

    log(f"Target data_id: {data_id}")

    cache_file = _cache_path(data_id, preferred_lang)
    if use_cache:
        cached = _cache_load(cache_file)
        if cached:
            log("Cache hit (< 24h) - skipping SerpApi calls")
            cached["cached"] = True
            return cached

    # 4. Fetch place details for metadata & official language
    place_info = fetch_serpapi_place_details(api_key, data_id)
    official_lang = infer_language(place_info.address)
    log(f"Detected place: '{place_info.name or 'Unknown'}' in language '{official_lang}'")

    # Prefer the canonical identifier returned by SerpApi for the reviews engine
    # (e.g. URL only had a decimal CID or /g/ id).
    review_id = data_id
    if not is_reviews_ready_id(review_id):
        review_id = place_info.data_id or place_info.place_id or data_id

    # 5. Fetch reviews in official language
    fetch_lang = official_lang
    reviews, total_count, next_token, raw_payload = fetch_serpapi_reviews(
        api_key=api_key,
        data_id=review_id,
        lang=fetch_lang
    )

    # Merge place info if first call was sparse
    if not place_info.name and isinstance(raw_payload.get("place_info"), dict):
        p_info = raw_payload["place_info"]
        place_info.name = p_info.get("title") or place_info.name
        place_info.address = p_info.get("address") or place_info.address

    # Fetch second page if total reviews are large (>500) and token exists
    if next_token and (total_count or 0) > NEXT_PAGE_THRESHOLD:
        try:
            more_reviews, _, _, _ = fetch_serpapi_reviews(
                api_key=api_key,
                data_id=review_id,
                lang=fetch_lang,
                page_token=next_token
            )
            # Deduplicate by author + text
            seen = {(r.author, r.text) for r in reviews}
            for mr in more_reviews:
                if (mr.author, mr.text) not in seen:
                    reviews.append(mr)
                    seen.add((mr.author, mr.text))
            log(f"Merged second page -> {len(reviews)} total fetched reviews")
        except Exception as exc:
            log(f"Second page fetch warning: {exc}")

    # English refetch if local guide coverage is thin and total reviews >= 10
    local_recent_guides = [r for r in reviews if r.is_local_guide]
    if (len(local_recent_guides) < MIN_RECENT_LOCAL_GUIDES
            and (total_count or 0) >= MIN_TOTAL_FOR_EN_REFETCH
            and fetch_lang != "en"):
        try:
            log("Local Guide coverage is thin; refetching in English for international insights...")
            en_reviews, _, _, _ = fetch_serpapi_reviews(
                api_key=api_key,
                data_id=review_id,
                lang="en"
            )
            # Merge (instead of replacing) so local-language / page-2 reviews aren't discarded
            seen = {(r.author, r.published_at or r.text) for r in reviews}
            added = 0
            for er in en_reviews:
                key = (er.author, er.published_at or er.text)
                if key not in seen:
                    reviews.append(er)
                    seen.add(key)
                    added += 1
            if added:
                fetch_lang = f"{fetch_lang}+en"
        except Exception as exc:
            log(f"English refetch warning: {exc}")

    if not reviews:
        return {
            "status": "no_reviews",
            "message": "This location does not have any public reviews on Google Maps.",
            "data_id": data_id,
            "place": asdict(place_info),
        }

    # 6. Curate and clip
    curated = curate_reviews(reviews)
    clipped = clip_reviews_for_prompt(curated)

    result = {
        "status": "success",
        "data_id": data_id,
        "resolved_url": resolved_url,
        "place": {
            "name": place_info.name or "Google Maps Place",
            "address": place_info.address,
            "type": place_info.types[0] if place_info.types else "unknown",
            "all_types": place_info.types,
            "average_rating": place_info.average_rating,
            "total_reviews": total_count or len(reviews),
            "rating_summary": place_info.rating_summary,
        },
        "stats": {
            "fetched_reviews": len(reviews),
            "curated_reviews": len(curated),
            "used_reviews": len(clipped),
            "fetch_language": fetch_lang,
            "preferred_summary_language": preferred_lang,
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
    _cache_save(cache_file, result)
    return result


# ---------------------------------------------------------------------------
# CLI Entrypoint
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract and curate Google Maps reviews for LLM summarization."
    )
    parser.add_argument("url", help="Google Maps link or text containing a Google Maps link")
    parser.add_argument("--api-key", help="SerpApi API Key (or set SERPAPI_API_KEY environment variable)")
    parser.add_argument("--lang", default="zh-TW", help="Preferred summary language code (default: zh-TW)")
    parser.add_argument("--markdown", action="store_true", help="Output high-density human/LLM-readable Markdown")
    parser.add_argument("--no-cache", action="store_true", help="Ignore the 24h review cache and force fresh SerpApi calls")
    parser.add_argument("--parse-only", action="store_true", help="Only parse URL and data_id without calling SerpApi")

    args = parser.parse_args()

    # Parse only mode
    if args.parse_only:
        raw_url = extract_google_maps_url(args.url)
        if not raw_url:
            print(json.dumps({"error": "No Google Maps URL found in input"}), file=sys.stdout)
            return 1
        resolved = resolve_short_url(raw_url)
        data_id = extract_maps_data_id(resolved)
        coords = extract_coordinates(resolved)
        print(json.dumps({
            "original_url": raw_url,
            "resolved_url": resolved,
            "data_id": data_id,
            "coordinates": coords
        }, indent=2, ensure_ascii=False), file=sys.stdout)
        return 0

    api_key = args.api_key or os.getenv("SERPAPI_API_KEY")
    if not api_key:
        print(
            "Error: SerpApi API Key is required. Provide via --api-key or set SERPAPI_API_KEY in environment.",
            file=sys.stderr
        )
        return 1

    try:
        result = run_pipeline(args.url, api_key, preferred_lang=args.lang, use_cache=not args.no_cache)
    except Exception as exc:
        print(json.dumps({"status": "error", "message": str(exc)}), file=sys.stdout)
        log(f"Execution failed: {exc}")
        return 1

    if result.get("status") == "no_reviews":
        print(json.dumps(result, indent=2, ensure_ascii=False), file=sys.stdout)
        return 2

    if args.markdown:
        p = result["place"]
        s = result["stats"]
        rating_str = f"⭐ {p['average_rating']:.1f}" if p.get("average_rating") else "⭐ 暫無評分"
        print(f"# {p['name']}")
        if p.get("address"):
            print(f"📍 地址: {p['address']}")
        print(f"🏷️ 分類: {p['type']} | {rating_str} (總評數: {p['total_reviews']})")
        print(f"📊 評論抽樣: 採集 {s['used_reviews']} 則關鍵評論 (來源語系: {s['fetch_language']})\n")
        print("## 精選評論清單 (Evidence Data)")
        print(result["formatted_reviews"])
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False), file=sys.stdout)

    return 0


if __name__ == "__main__":
    sys.exit(main())
