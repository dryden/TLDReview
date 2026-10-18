# 🎨 Meta Muse Integration Guide

**English** | **[繁體中文](README.zh-TW.md)**

Meta Muse provides an isolated Linux VM sandbox protected by the Sentinel network firewall.

---

## Quick Setup Steps

1. **Provide Your API Key**: Set or import `SERPAPI_API_KEY` in your Muse environment.
2. **Paste the Setup Prompt**: Copy [`setup_prompt.md`](./setup_prompt.md) into your Muse chat.
3. **Approve Sentinel Network Allowlist**:
   - When Muse runs the script and Sentinel prompts for outbound network approval, allow:
     - `maps.app.goo.gl`, `goo.gl`, `share.google`, `www.google.com` (Short URL resolution)
     - `serpapi.com` (Google Maps Reviews API)
     - `raw.githubusercontent.com` (One-time script download)

---

## Example Commands
```bash
# 1. Direct Google Maps URL
python3 ~/tldreview_tool.py "https://maps.app.goo.gl/sample" --lang en --markdown

# 2. "Nearby" search with user coordinates (auto-selects best match)
python3 ~/tldreview_tool.py "FamilyMart near me" --lat 25.0330 --lng 121.5654 --lang en --markdown

# 3. Store name / address extracted from a signboard or menu photo
python3 ~/tldreview_tool.py "Lao Zhang Beef Noodle Taipei Da'an" --lang en --markdown
```
