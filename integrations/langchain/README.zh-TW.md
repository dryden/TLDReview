# 🦜🔗 LangChain & CrewAI 整合指南

**[English](README.md)** | **繁體中文**

本模組提供一鍵包裝好的 `@tool`，可直接傳入 LangChain Agent 或 CrewAI Agent。

```python
import os
from integrations.langchain.tool import tldreview_maps_tool

# 直接加入 Agent 的 tools 清單
tools = [tldreview_maps_tool]
```
