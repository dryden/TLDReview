# 🔮 MCP (Model Context Protocol) 整合指南

透過 Anthropic 主導的 MCP 協議，你可以將 TLDReview 直接掛載到 **Claude Desktop**、**Cursor** 或 **Antigravity IDE** 中。

---

## 安裝與依賴
```bash
pip install mcp
```

## 配置 Claude Desktop
編輯 `~/Library/Application Support/Claude/claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "tldreview": {
      "command": "python3",
      "args": [
        "/絕對路徑/TLDReview/integrations/mcp/server.py"
      ],
      "env": {
        "SERPAPI_API_KEY": "你的_SERPAPI_KEY"
      }
    }
  }
}
```

配置完成後重啟 Claude Desktop，Claude 便能在對話中自動辨識 Google Maps 網址並呼叫 TLDReview 工具！
