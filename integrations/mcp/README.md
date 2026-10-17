# 🔮 MCP (Model Context Protocol) Integration Guide

**English** | **[繁體中文](README.zh-TW.md)**

Using the Model Context Protocol (MCP), you can mount TL;DReview directly into **Claude Desktop**, **Cursor**, or **Antigravity IDE**.

---

## Installation & Dependencies
```bash
pip install mcp
# Or install via project extras:
pip install -e ".[mcp]"
```

## Configure Claude Desktop
Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "tldreview": {
      "command": "python3",
      "args": [
        "/absolute/path/to/TLDReview/integrations/mcp/server.py"
      ],
      "env": {
        "SERPAPI_API_KEY": "YOUR_SERPAPI_KEY"
      }
    }
  }
}
```

Restart Claude Desktop after saving. Claude will now automatically recognize Google Maps links in your conversation and invoke the `summarize_google_maps_reviews` MCP tool!
