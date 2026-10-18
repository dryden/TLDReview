"""TL;DReview LangChain / CrewAI Tool Adapter."""
import os
import sys
from typing import Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
from core.tldreview_tool import (
    format_markdown_output,
    format_vault_markdown_output,
    resolve_api_key,
    run_compare_pipeline,
    run_pipeline,
    run_top_discovery_pipeline,
    vault_escrow_setup,
    vault_query_places,
    vault_recover_from_passphrase,
    vault_save_place,
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


@tool
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
    """Save or update a restaurant in the user's E2EE Personal Dining Vault (`wishlist`, `liked`, or `disliked`)."""
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
        return f"Error: {exc}"


@tool
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
    """Query the user's E2EE Personal Dining Vault by status (`wishlist`, `liked`, `disliked`, `all`), keyword, tag, or GPS proximity (0 SerpApi credits)."""
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
        return f"Error: {exc}"


@tool
def tldreview_vault_escrow(
    passphrase: str,
    sync_key: Optional[str] = None,
) -> str:
    """Escrow the user's Sync Key to the Cloud Relay encrypted with a memorable recovery passphrase.

    Follow the in-chat terminal-free flow:
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
        return f"Error: {exc}"


@tool
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
        return f"Error: {exc}"

