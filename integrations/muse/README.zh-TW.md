# 🎨 Meta Muse 串接教學

**[English](README.md)** | **繁體中文**

Meta Muse 具備獨立的 Linux VM 沙盒環境與 Sentinel 網路防護機制。

---

## 快速設定步驟

1. **提供金鑰**：在對話中設定或匯入 `SERPAPI_API_KEY`。
2. **複製 Prompt**：複製 [`setup_prompt.zh-TW.md`](./setup_prompt.zh-TW.md) 給 Muse。
3. **開通 Sentinel 網域白名單**：
   - 當 Muse 執行腳本時，若 Sentinel 彈出確認視窗，請允許以下網域：
     - `maps.app.goo.gl`, `goo.gl`, `share.google`, `www.google.com` (短網址還原)
     - `serpapi.com` (取得評論)
     - `raw.githubusercontent.com` (首次下載腳本)

---

## 常用指令範例
```bash
# 1. Google Maps 網址查詢
python3 ~/tldreview_tool.py "https://maps.app.goo.gl/sample" --markdown

# 2. 「附近店家」座標搜尋（自動進入最符合店家評論）
python3 ~/tldreview_tool.py "附近的全家便利商店" --lat 25.0330 --lng 121.5654 --markdown

# 3. 招牌／菜單照片辨識後以店名或地址搜尋
python3 ~/tldreview_tool.py "老張牛肉麵 台北市大安區" --markdown
```
