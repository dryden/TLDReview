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

## Daily Usage (3 Input Modes)
1. **🔗 Paste a Google Maps Link**:
   ```text
   Is this place worth visiting? https://maps.app.goo.gl/xxxxxx
   ```
2. **📍 Ask for a "Nearby" Store**:
   ```text
   Check the reviews for FamilyMart near me
   ```
   *(Grok acquires your current coordinates, searches Google Maps, and jumps straight into the best-matching store's reviews)*
3. **📸 Send a Photo of a Signboard or Menu**:
   Snap a photo of a store's **signboard (招牌), menu (菜單), or business card** and send it to Grok—it will recognize the store name/address, lock onto the best match, and generate a high-density intelligence report!
