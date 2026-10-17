#!/usr/bin/env python3
"""TL;DReview MCP (Model Context Protocol) Server.

Enables Claude Desktop, Cursor, Antigravity, or any MCP client to directly
inspect Google Maps links and get structured review digests.
"""
import os
import sys

# Ensure core is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
from core.tldreview_tool import format_markdown_output, run_pipeline

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
def summarize_google_maps_reviews(url: str, lang: str = "zh-TW") -> str:
    """Extract and curate Google Maps reviews for a given location URL.

    Args:
        url: The Google Maps URL (full or short link like maps.app.goo.gl).
        lang: Target summary language (default: zh-TW).

    Returns:
        Structured high-density review evidence block ready for analysis.
    """
    api_key = os.getenv("SERPAPI_API_KEY")
    if not api_key:
        return "Error: SERPAPI_API_KEY environment variable is not set."

    try:
        result = run_pipeline(url, api_key, preferred_lang=lang)
    except Exception as exc:
        return f"Error executing TLDReview pipeline: {exc}"

    if result.get("status") == "no_reviews":
        return f"Location '{result.get('place', {}).get('name')}' has no public reviews on Google Maps."

    return format_markdown_output(result, include_agent_instructions=True)

if __name__ == "__main__":
    mcp.run()
