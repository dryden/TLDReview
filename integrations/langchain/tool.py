"""TL;DReview LangChain / CrewAI Tool Adapter."""
import os
import sys
from typing import Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
from core.tldreview_tool import format_markdown_output, resolve_api_key, run_pipeline

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
    lang: str = "zh-TW",
    sort_by: str = "qualityScore",
    save_quota: bool = False,
) -> str:
    """Extract and curate Google Maps reviews from a URL, nearby/store search query (with optional latitude/longitude), or signboard/menu photo."""
    api_key = resolve_api_key()
    if not api_key:
        return "Error: Missing SERPAPI_API_KEY"
    target_input = (query or url or "").strip()
    try:
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
