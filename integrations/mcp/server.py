#!/usr/bin/env python3
"""TL;DReview MCP (Model Context Protocol) Server.

Enables Claude Desktop, Cursor, Antigravity, or any MCP client to directly
inspect Google Maps links and get structured review digests.
"""
import os
import sys

# Ensure core is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
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
    url: str,
    lang: str = "zh-TW",
    sort_by: str = "qualityScore",
    save_quota: bool = False,
) -> str:
    """Extract and curate Google Maps reviews for a given location URL.

    Args:
        url: The Google Maps URL (full or short link like maps.app.goo.gl).
        lang: Target summary language (default: zh-TW).
        sort_by: Sort order: qualityScore (relevant), newest, lowest, or highest.
        save_quota: If True, use only 1 SerpApi credit per place.

    Returns:
        Structured high-density review evidence block ready for analysis.
    """
    api_key = resolve_api_key()
    if not api_key:
        return "Error: SERPAPI_API_KEY environment variable or .env file is not set."

    try:
        result = run_pipeline(
            url,
            api_key,
            preferred_lang=lang,
            sort_by=sort_by,
            save_quota=save_quota,
        )
    except Exception as exc:
        return f"Error executing TLDReview pipeline: {exc}"

    if result.get("status") == "no_reviews":
        return f"Location '{result.get('place', {}).get('name')}' has no public reviews on Google Maps."

    return format_markdown_output(result, include_agent_instructions=True)


@mcp.prompt()
def tldreview_analysis_prompt(lang: str = "zh-TW") -> str:
    """Load the official TL;DReview anti-hallucination system prompt template."""
    prompt_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../prompts"))
    filename = "SYSTEM_PROMPT_EN.md" if lang.lower().startswith("en") else "SYSTEM_PROMPT.md"
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
