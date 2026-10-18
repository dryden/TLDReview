#!/usr/bin/env python3
"""TL;DReview MCP (Model Context Protocol) Server.

Enables Claude Desktop, Cursor, Antigravity, or any MCP client to directly
inspect Google Maps links and get structured review digests.
"""
import os
import sys

# Ensure core is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
from typing import Optional
from core.tldreview_tool import format_markdown_output, resolve_api_key, run_pipeline

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print(
        "MCP SDK not found. Install it with: pip install mcp",
        file=sys.stderr
    )
    sys.exit(1)

mcp = FastMCP("tldreview")

@mcp.tool()
def summarize_google_maps_reviews(
    url: str = "",
    query: str = "",
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    image_path: Optional[str] = None,
    lang: str = "zh-TW",
    sort_by: str = "qualityScore",
    save_quota: bool = False,
) -> str:
    """Extract and curate Google Maps reviews from a URL, store/nearby search query, or photo.

    Supports three input modes:
    1. Google Maps URL: Pass `url` (full link or short link like maps.app.goo.gl).
    2. Nearby / Keyword Search (e.g. "附近的全家便利商店" or "台北大安區老張牛肉麵"):
       Pass the store name/keyword in `query` (or `url`), and pass the user's current
       coordinates in `latitude` and `longitude` when searching nearby places.
    3. Store Photo (e.g. signboard 招牌 or menu 菜單 with store name/address):
       Extract the store name and/or address from the user's photo via Vision and pass it
       in `query` (along with `latitude`/`longitude` or `image_path` if available).

    Args:
        url: Google Maps URL, or a store name / search query string.
        query: Store name, address, or search keyword (used if `url` is omitted).
        latitude: Optional user latitude for nearby place search (e.g. 25.0330).
        longitude: Optional user longitude for nearby place search (e.g. 121.5654).
        image_path: Optional local file path to a store signboard/menu photo (extracts EXIF GPS or OCR).
        lang: Target summary language (default: zh-TW).
        sort_by: Sort order: qualityScore (relevant), newest, lowest, or highest.
        save_quota: If True, minimize SerpApi credit usage.

    Returns:
        Structured high-density review evidence block ready for analysis.
    """
    api_key = resolve_api_key()
    if not api_key:
        return "Error: SERPAPI_API_KEY environment variable or .env file is not set."

    target_input = (query or url or "").strip()
    try:
        result = run_pipeline(
            target_input,
            api_key,
            preferred_lang=lang,
            sort_by=sort_by,
            save_quota=save_quota,
            lat=latitude,
            lng=longitude,
            image_path=image_path,
        )
    except Exception as exc:
        return f"Error executing TLDReview pipeline: {exc}"

    if result.get("status") == "no_reviews":
        return f"Location '{result.get('place', {}).get('name')}' has no public reviews on Google Maps."

    return format_markdown_output(result, include_agent_instructions=True)


@mcp.prompt()
def tldreview_analysis_prompt(lang: str = "en") -> str:
    """Load the official TL;DReview anti-hallucination system prompt template."""
    prompt_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../prompts"))
    filename = "SYSTEM_PROMPT.zh-TW.md" if lang.lower().startswith("zh") else "SYSTEM_PROMPT.md"
    prompt_path = os.path.join(prompt_dir, filename)
    try:
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return "Analyze the Google Maps reviews strictly based on evidence without hallucination."


def main() -> None:
    """Entrypoint for tldreview-mcp CLI command."""
    mcp.run()


if __name__ == "__main__":
    main()
