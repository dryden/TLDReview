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

## Example Command
```bash
# Test connection and review extraction
python3 ~/tldreview_tool.py "https://maps.app.goo.gl/sample" --lang en --markdown
```
