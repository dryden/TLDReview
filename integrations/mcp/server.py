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
from core.tldreview_tool import (
    format_markdown_output,
    format_vault_markdown_output,
    resolve_api_key,
    run_compare_pipeline,
    run_pipeline,
    run_top_discovery_pipeline,
    vault_delete_place,
    vault_escrow_setup,
    vault_query_places,
    vault_recover_from_passphrase,
    vault_save_place,
)

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
    compare_targets: Optional[list[str]] = None,
    top_n: int = 0,
    lang: str = "zh-TW",
    sort_by: str = "qualityScore",
    save_quota: bool = False,
) -> str:
    """Extract and curate Google Maps reviews from a URL, store/nearby search query, photo, multi-venue comparison, or area top-N scan.

    Supports five workflows:
    1. Google Maps URL: Pass `url` (full link or short link like maps.app.goo.gl).
    2. Nearby / Keyword Search (e.g. "附近的全家便利商店" or "台北大安區老張牛肉麵"):
       Pass the store name/keyword in `query` (or `url`), and pass the user's current
       coordinates in `latitude` and `longitude` when searching nearby places.
    3. Store Photo (e.g. signboard 招牌 or menu 菜單 with store name/address):
       Extract the store name and/or address from the user's photo via Vision and pass it
       in `query` (along with `latitude`/`longitude` or `image_path` if available).
    4. Multi-Venue Side-by-Side Comparison (`compare_targets`):
       Pass 2+ URLs or store queries in `compare_targets` (e.g. `["林東芳牛肉麵", "永康牛肉麵"]`)
       to fetch in parallel with scaled token budgets and compare side-by-side.
    5. Area Discovery + Auto-Audit (`top_n`):
       Set `top_n=3` (with `query` like "附近好吃的拉麵" and optional `latitude`/`longitude`)
       to strip sponsored ads, audit the top N nearby stores for review manipulation, and crown the true organic winner.

    Args:
        url: Google Maps URL, or a store name / search query string.
        query: Store name, address, or search keyword (used if `url` is omitted).
        latitude: Optional user latitude for nearby place search (e.g. 25.0330).
        longitude: Optional user longitude for nearby place search (e.g. 121.5654).
        image_path: Optional local file path to a store signboard/menu photo (extracts EXIF GPS or OCR).
        compare_targets: Optional list of 2+ URLs or store queries for side-by-side comparison.
        top_n: If > 0 (e.g. 3), scan top N nearby organic candidates and run anti-manipulation audit.
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
        if compare_targets and len(compare_targets) >= 2:
            result = run_compare_pipeline(
                targets=compare_targets,
                api_key=api_key,
                preferred_lang=lang,
                sort_by=sort_by,
                save_quota=save_quota,
                lat=latitude,
                lng=longitude,
            )
        elif top_n and top_n > 0:
            result = run_top_discovery_pipeline(
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


@mcp.tool()
def tldreview_vault_save(
    place_id: str = "",
    name: str = "",
    status: str = "wishlist",
    address: str = "",
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    google_rating: Optional[float] = None,
    user_rating: Optional[float] = None,
    tags: str = "",
    must_try: str = "",
    red_flags: str = "",
    insider_tips: str = "",
    user_notes: str = "",
    sync_key: Optional[str] = None,
) -> str:
    """Save or update a restaurant in the user's E2EE Personal Dining Vault (`wishlist`, `liked`, or `disliked`).

    Automatically encrypts and syncs across devices using `TLDREVIEW_SYNC_KEY` (or generates a new key on first save).
    If a new `TLDREVIEW_SYNC_KEY` is generated, ALWAYS show it to the user and store it in Agent memory!

    Args:
        place_id: Google Maps data_id or place_id (if known from a previous digest).
        name: Restaurant / store name (required if place_id is omitted).
        status: One of `wishlist` (default), `liked`, or `disliked`.
        address: Store address.
        lat: Store latitude (for future nearby distance filtering).
        lng: Store longitude.
        google_rating: Google Maps average rating.
        user_rating: User's personal rating (1.0 - 5.0).
        tags: Comma-separated tags (e.g. "牛肉麵,大安區,長輩友善").
        must_try: Comma-separated must-try dishes (e.g. "紅燒半筋半肉麵,花椒牛油").
        red_flags: Comma-separated caveats/red flags (e.g. "湯頭偏鹹,冷氣較弱").
        insider_tips: Comma-separated insider tips (e.g. "只收現金,11:45前到免排隊").
        user_notes: User's personal notes or dining memory.
        sync_key: Optional E2EE sync key (`tld_<vault_id>_<master_secret>`).
    """
    try:
        res = vault_save_place(
            place_id=place_id or None,
            name=name or None,
            address=address or None,
            lat=lat,
            lng=lng,
            google_rating=google_rating,
            status=status,
            user_rating=user_rating,
            tags=tags or None,
            must_try=must_try or None,
            red_flags=red_flags or None,
            insider_tips=insider_tips or None,
            user_notes=user_notes or None,
            sync_key=sync_key,
            api_key=resolve_api_key(),
        )
        return format_vault_markdown_output(res)
    except Exception as exc:
        return f"Error saving to TLDReview Vault: {exc}"


@mcp.tool()
def tldreview_vault_query(
    status: str = "all",
    keyword: Optional[str] = None,
    tag: Optional[str] = None,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    radius_km: float = 5.0,
    limit: int = 10,
    sync_key: Optional[str] = None,
) -> str:
    """Query the user's E2EE Personal Dining Vault by status (`wishlist`, `liked`, `disliked`, `all`), keyword, tag, or GPS proximity (0 SerpApi credits).

    Use this tool FIRST when the user asks about their saved restaurants, pocket list, or what is on their wishlist nearby!

    Args:
        status: Filter by `wishlist`, `liked`, `disliked`, or `all` (default: `all`).
        keyword: Optional keyword matching name, address, dishes, tags, or personal notes.
        tag: Optional tag filter (e.g. "牛肉麵" or "大安區").
        lat: Optional user latitude for Haversine distance filtering & sorting.
        lng: Optional user longitude for Haversine distance filtering & sorting.
        radius_km: Search radius in kilometers when `lat`/`lng` are provided (default: 5.0).
        limit: Maximum number of places to return (default: 10).
        sync_key: Optional E2EE sync key (`tld_<vault_id>_<master_secret>`).
    """
    try:
        res = vault_query_places(
            status=status,
            keyword=keyword,
            tag=tag,
            lat=lat,
            lng=lng,
            radius_km=radius_km if (lat is not None and lng is not None) else None,
            limit=limit,
            sync_key=sync_key,
        )
        return format_vault_markdown_output(res)
    except Exception as exc:
        return f"Error querying TLDReview Vault: {exc}"


@mcp.tool()
def tldreview_vault_escrow(
    passphrase: str,
    sync_key: Optional[str] = None,
) -> str:
    """Escrow the user's Sync Key to the Cloud Relay encrypted with a memorable recovery passphrase.

    In chat, follow the terminal-free flow:
    1. First give honest disclosure: "This passphrase will remain in our chat history. Its purpose is to let you recover your vault when switching devices or after a VM reset—not to protect against someone who can read this chat. If that's okay with you, just tell me a memorable phrase right here."
    2. Run this tool with the passphrase. When reporting success, DO NOT echo the passphrase back in chat; only provide the returned escrow_id.
    3. Remind the user: "Next time you switch devices, just tell your new AI that exact phrase and it can restore your vault in one step."

    Args:
        passphrase: A memorable recovery passphrase (at least 10 chars or 3 words).
        sync_key: Optional explicit sync key to escrow.
    """
    try:
        res = vault_escrow_setup(passphrase=passphrase, sync_key=sync_key)
        return format_vault_markdown_output(res)
    except Exception as exc:
        return f"Error backing up Vault with passphrase: {exc}"


@mcp.tool()
def tldreview_vault_recover(
    passphrase: str,
) -> str:
    """Recover the user's Sync Key and restore all saved places on a new device or VM using their recovery passphrase.

    Args:
        passphrase: The memorable recovery passphrase set previously.
    """
    try:
        res = vault_recover_from_passphrase(passphrase=passphrase)
        return format_vault_markdown_output(res)
    except Exception as exc:
        return f"Error recovering Vault with passphrase: {exc}"


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

