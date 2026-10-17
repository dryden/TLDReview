# 🦜🔗 LangChain & CrewAI Integration Guide

**English** | **[繁體中文](README.zh-TW.md)**

This module provides a pre-wrapped `@tool` ready to be passed directly into a LangChain or CrewAI Agent.

```python
import os
from integrations.langchain.tool import tldreview_maps_tool

# Add directly to your Agent's tools list
tools = [tldreview_maps_tool]
```
