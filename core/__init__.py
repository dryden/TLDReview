"""TL;DReview Core Package.

Zero-dependency Google Maps review extraction, curation, and intelligence engine.
"""
from .tldreview_tool import (
    extract_google_maps_url,
    extract_maps_data_id,
    resolve_short_url,
    infer_language,
    fetch_serpapi_place_details,
    fetch_serpapi_reviews,
    curate_reviews,
    clip_reviews_for_prompt,
    format_markdown_output,
    load_dotenv_if_present,
    normalize_sort_by,
    resolve_api_key,
    run_pipeline,
    PlaceInfo,
    Review,
)

__all__ = [
    "extract_google_maps_url",
    "extract_maps_data_id",
    "resolve_short_url",
    "infer_language",
    "fetch_serpapi_place_details",
    "fetch_serpapi_reviews",
    "curate_reviews",
    "clip_reviews_for_prompt",
    "format_markdown_output",
    "load_dotenv_if_present",
    "normalize_sort_by",
    "resolve_api_key",
    "run_pipeline",
    "PlaceInfo",
    "Review",
]
