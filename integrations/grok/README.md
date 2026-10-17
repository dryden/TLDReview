# 🤖 xAI Grok Bot Integration Guide

**English** | **[繁體中文](README.zh-TW.md)**

xAI Grok Bot provides a persistent Cloud Computer sandbox and encrypted Secret management, making it an ideal environment for running TL;DReview.

---

## 3-Step Quick Setup

### Step 1: Create a Grok Bot and Upload the Tool Script
1. Open or create a dedicated Bot conversation in Grok.
2. Upload [`core/tldreview_tool.py`](../../core/tldreview_tool.py) as an attachment to Grok.

### Step 2: Configure the SerpApi Secret
Add a new Secret in the Grok Bot interface:
- **Key**: `SERPAPI_API_KEY`
- **Value**: `<Your SerpApi API Key>`

*(Don't have a key yet? Sign up at [SerpApi.com](https://serpapi.com) to get 250 free searches per month.)*

### Step 3: Paste the Setup Prompt
Open [`setup_prompt.md`](./setup_prompt.md), copy the prompt block, and send it to Grok. Grok will install `~/tldreview/tldreview_tool.py` in its sandbox and configure the anti-hallucination analysis brain!

---

## Daily Usage
Simply paste any Google Maps link into your Grok chat:
```text
Is this place worth visiting? https://maps.app.goo.gl/xxxxxx
```
Grok will automatically run the tool, fetch real customer reviews, and generate a high-density intelligence report!
