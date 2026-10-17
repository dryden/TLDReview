# 🤖 xAI Grok Bot 串接教學

Grok Bot 具備持續性的 Cloud Computer 沙盒環境與 Secret 安全管理，是運行 TLDReview 的理想載體。

---

## 3 步快速設定

### 步驟 1：建立 Grok Bot 並上傳工具腳本
1. 在 Grok 中開啟或建立一個專門的 Bot 對話。
2. 將本專案的 [`core/tldreview_tool.py`](../../core/tldreview_tool.py) 作為附件上傳給 Grok。

### 步驟 2：設定 SerpApi Secret
在 Grok Bot 介面中新增 Secret：
- Key: `SERPAPI_API_KEY`
- Value: `<你的 SerpApi API Key>`

*(若無金鑰，可至 [SerpApi.com](https://serpapi.com) 免費註冊領取每月免費額度)*

### 步驟 3：複製設定 Prompt
打開 [`setup_prompt.md`](./setup_prompt.md)，完整複製貼給 Grok。Grok 會在沙盒中建立 `~/tldreview/tldreview_tool.py` 並載入避雷大腦！

---

## 日常使用方式
直接在 Grok 對話框中貼上任何 Google Maps 連結，例如：
```text
這家店值得去嗎？https://maps.app.goo.gl/xxxxxx
```
Grok 會自動調用工具抓取真實評論，並產出高密度避雷報告！
