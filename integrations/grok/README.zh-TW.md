# 🤖 xAI Grok Bot 串接教學

**[English](README.md)** | **繁體中文**

Grok Bot 具備持續性的 Cloud Computer 沙盒環境與 Secret 安全管理，是運行 TL;DReview 的理想載體。

---

## 3 步快速設定

### 步驟 1：建立 Grok Bot 並上傳工具腳本
1. 在 Grok 中開啟或建立一個專門的 Bot 對話。
2. 將本專案的 [`core/tldreview_tool.py`](../../core/tldreview_tool.py) 作為附件上傳給 Grok。

### 步驟 2：設定 SerpApi Secret
在 Grok Bot 介面中新增 Secret：
- Key: `SERPAPI_API_KEY`
- Value: `<你的 SerpApi API Key>`

*(若無金鑰，可至 [SerpApi.com](https://serpapi.com) 免費註冊領取每月 250 次免費額度)*

### 步驟 3：複製設定 Prompt
打開 [`setup_prompt.zh-TW.md`](./setup_prompt.zh-TW.md)，完整複製貼給 Grok。Grok 會在沙盒中建立 `~/tldreview/tldreview_tool.py` 並載入避雷大腦！

---

## 日常使用方式（支援 3 種查詢情境）
1. **🔗 貼上 Google Maps 連結**：
   ```text
   這家店值得去嗎？https://maps.app.goo.gl/xxxxxx
   ```
2. **📍 直接說「附近的店家」**：
   ```text
   幫我查一下附近的全家便利商店評價
   ```
   *(Grok 會取得你目前的座標，搜尋並直接進入最符合的店家分析評論)*
3. **📸 傳送店家招牌或菜單照片**：
   直接拍下店門口的**招牌、菜單或名片**傳給 Grok，Grok 會自動辨識店名與地址，鎖定最符合的店家並產出高密度避雷報告！
