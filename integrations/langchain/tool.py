"""TL;DReview LangChain / CrewAI Tool Adapter."""
import os
import sys
from typing import Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
from core.tldreview_tool import (
    format_markdown_output,
    resolve_api_key,
    run_compare_pipeline,
    run_pipeline,
    run_top_discovery_pipeline,
)

try:
    from langchain.tools import tool
except ImportError:
    # Fallback dummy decorator if langchain is not installed
    def tool(func):
        return func

@tool
def tldreview_maps_tool(
    url: str = "",
    query: str = "",
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    image_path: Optional[str] = None,
    compare_targets: Optional[list[str]] = None,
    top_n: int = 0,
    lang: str = "zh-TW",
    sort_by: str = "qualityScore",
    save_quota: bool = False,
) -> str:
    """Extract and curate Google Maps reviews from a URL, nearby/store search query, signboard/menu photo, multi-venue comparison (`compare_targets`), or area top-N scan (`top_n`)."""
    api_key = resolve_api_key()
    if not api_key:
        return "Error: Missing SERPAPI_API_KEY"
    target_input = (query or url or "").strip()
    try:
        if compare_targets and len(compare_targets) >= 2:
            res = run_compare_pipeline(
                targets=compare_targets,
                api_key=api_key,
                preferred_lang=lang,
                sort_by=sort_by,
                save_quota=save_quota,
                lat=latitude,
                lng=longitude,
            )
        elif top_n and top_n > 0:
            res = run_top_discovery_pipeline(
                query=target_input,
                api_key=api_key,
                top_n=top_n,
                preferred_lang=lang,
                sort_by=sort_by,
                save_quota=save_quota,
                lat=latitude,
                lng=longitude,
            )
        else:
            res = run_pipeline(
                raw_input=target_input,
                api_key=api_key,
                preferred_lang=lang,
                sort_by=sort_by,
                save_quota=save_quota,
                lat=latitude,
                lng=longitude,
                image_path=image_path,
            )
        if res.get("status") == "no_reviews":
            return "No public reviews found for this place."
        return format_markdown_output(res, include_agent_instructions=True)
    except Exception as exc:
        return f"Error: {exc}"
