# Contributing to TLDReview

歡迎為 TLDReview 提供貢獻！無論是回報 Bug、改善核心清洗演算法，或是新增其他 AI Agent 的適配層（Adapter），我們都非常歡迎。

---

## 擴充新的 Agent 整合（Adding a New Agent Adapter）

若你想新增某個 AI Agent（例如 Coze、Dify、OpenAI Actions、Discord Bot、Telegram Bot 等）：

1. 在 `integrations/` 下建立新的子目錄，例如 `integrations/my_agent/`。
2. 包含以下兩個核心檔案：
   - `README.md`：該平台的操作指引與金鑰管理說明。
   - `setup_prompt.md` 或 `adapter.py`：啟動 Prompt 或程式碼橋接器。
3. 確保你的 Agent 調用邏輯符合 `prompts/SYSTEM_PROMPT.md` 所規範的分析標準：
   - 嚴格反幻覺（Anti-hallucination）
   - 識別評分操縱（五星送贈品）
   - 結構化 6 大維度排版
4. 發起 Pull Request！

---

## 核心規則
- `core/tldreview_tool.py` 必須保持 **Zero Dependencies**（僅使用 Python 3 標準庫），以便隨時能在無網際網路安裝套件的極簡沙盒中直接運作。
