"""TL;DReview LangChain / CrewAI Tool Adapter."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
from core.tldreview_tool import run_pipeline

try:
    from langchain.tools import tool
except ImportError:
    # Fallback dummy decorator if langchain is not installed
    def tool(func):
        return func

@tool
def tldreview_maps_tool(url: str, lang: str = "zh-TW") -> str:
    """Extract and curate Google Maps reviews for anti-hallucinatory agent reasoning."""
    api_key = os.getenv("SERPAPI_API_KEY")
    if not api_key:
        return "Error: Missing SERPAPI_API_KEY"
    try:
        res = run_pipeline(raw_input=url, api_key=api_key, preferred_lang=lang)
        if res.get("status") == "no_reviews":
            return "No public reviews found for this place."
        return res.get("formatted_reviews", "")
    except Exception as exc:
        return f"Error: {exc}"
