# 🦊 TL;DReview (Too Long; Didn't Review)

<div align="center">

**Turn messy Google Maps reviews into actionable, anti-hallucination intelligence for any AI Agent.**  
*一鍵將雜亂冗長的 Google Maps 評論，提煉成極具避雷價值與洞察深度的結構化報告。*

🌐 **[專案官網 (Official Website)](https://tldreview.app/)**

[![Website](https://img.shields.io/badge/Website-tldreview.app-blue?logo=googlechrome&logoColor=white)](https://tldreview.app/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-brightgreen.svg)](https://www.python.org/)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-0-success.svg)](core/tldreview_tool.py)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

</div>

---

## 🦊 TL;DReview 是什麼？

**TL;DReview**（官網：[https://tldreview.app/](https://tldreview.app/)）是一個專為 AI Agent 與個人助手設計的評論分析工具。貼上 Google Maps 網址，它就會從數百則又長又雜的評論中，整理出一份結構化報告：**必點招牌、優點、缺點與避雷、內行人祕笈、營運規則、交通停車**，讓你在出發前就掌握一個地點的完整細節。

---

## 🆚 Google Maps 已經有評論摘要，為什麼還需要 TL;DReview？

Google Maps 的評論摘要最多只有三行，看完之後往往還是得回頭翻完整評論。

![Google Maps 評論摘要與 TL;DReview 報告的對比](docs/images/gmaps-vs-tldreview.png)

TL;DReview 會從眾多評論中過濾、整理出該地點的**優點、缺點、內行人祕笈**等分類內容，讓你快速掌握一間店的細節。

此外你還可以：
- 💬 **隨時追問 AI**：AI 會從評論中找出相關資料來回覆你。
- 🎛️ **自訂輸出分類**：直接請 AI 調整未來每次報告的內容分類方式，打造最貼近你需求的客製化體驗。

---

## ✨ 核心特色

去餐廳、咖啡廳或熱門景點時，Google Maps 的星級常常被「五星打卡送小菜/折價券」灌水；數百則評論又長又雜，根本看不完。TL;DReview 的做法是：

1. **🛡️ 嚴格反幻覺（Anti-Hallucination）**：所有優勢、痛點、必點招牌必須 100% 來自真實評論證據；無證據的維度直接省略。
2. **🔍 評分操縱識別（Bias Detection）**：自動揪出「五星送飲料」等刷評行為，給出最客觀的避雷提醒。
3. **🚀 零依賴核心（Zero Dependencies）**：僅使用 Python 3 標準函式庫，任何 Linux VM、Docker 或 AI 沙盒免安裝套件即可秒級執行。
4. **🧩 全方位 Agent 支援**：已適配 **xAI Grok Bot**、**Meta Muse**、**Claude / Cursor (MCP)**、**LangChain / CrewAI**，並支援一鍵擴充。

---

## 🗺️ 架構與運作流程

```mermaid
graph TD
    User([使用者傳送 Google Maps 網址]) --> Agent[AI Agent / Grok / Muse / Claude]
    Agent --> Core[core/tldreview_tool.py]
    Core --> ShortURL[短網址解析 & CID 提取]
    Core --> SerpApi[SerpApi 抓取最新與高權重評論]
    Core --> Filter[評論篩選、防爆 Token 密度壓縮]
    Filter --> Prompt[prompts/SYSTEM_PROMPT.md]
    Prompt --> Output[🦊 深度避雷與推薦報告]
```

---

## 🔑 使用前準備：取得 SerpApi API Key

TL;DReview 透過 [SerpApi](https://serpapi.com/) 抓取 Google Maps 評論，使用前請先：

1. 前往 [SerpApi Dashboard](https://serpapi.com/dashboard) 註冊帳號。
2. 在 Dashboard 取得你的 **API Key**。
3. 將 API Key 設定為環境變數 `SERPAPI_API_KEY`（或依各 Agent 整合指南設定為 Secret）。

> [!TIP]
> SerpApi 免費方案**每月可免費查詢 250 次**，個人使用通常綽綽有餘。

---

## ⚡ 支援的 AI Agent 快速開始

| 平台 / Agent | 整合方式 | 特色 | 指南連結 |
| :--- | :--- | :--- | :--- |
| **xAI Grok Bot** | Cloud Computer 附件 | 具備持久化沙盒、Secret 密鑰保護 | [👉 Grok 串接指南](integrations/grok/) |
| **Meta Muse** | Linux VM 沙盒 | 具備 Sentinel 安全連線提示 | [👉 Muse 串接指南](integrations/muse/) |
| **Claude / Cursor** | Model Context Protocol (MCP) | 一鍵掛載至本機 Claude Desktop / IDE | [👉 MCP 串接指南](integrations/mcp/) |
| **LangChain / CrewAI** | Python `@tool` 裝飾器 | 快速納入 Multi-Agent 鏈路 | [👉 LangChain 指南](integrations/langchain/) |
| **CLI / 本機終端** | 直接執行獨立腳本 | 支援 Markdown 與 JSON 輸出 | [👉 Core CLI 指南](#️-本機獨立執行-cli) |

---

## 📊 輸出報告預覽

```text
🦊 TL;DReview 深度報告：老張家常牛肉麵
📍 地址：台北市大安區新生南路一段
🏷️ 類型：麵食餐廳｜⭐ 4.3（共 1,820 則評價，分析 24 則核心評論）

---

🍜 必點招牌 / 核心亮點
・紅燒半筋半肉麵：牛筋燉至軟爛，腱子肉厚切入味，湯頭甘醇帶淡淡中藥香（極高共識）。
・自製花椒牛油：加半匙能大幅提升香氣與層次感。

👍 優勢與推薦點
・出餐節奏極快，客滿時入座約 5 分鐘即上桌。
・小菜選擇多元，涼拌小黃瓜與滷豆干深受好評。

👎 缺點與避雷提醒
・湯頭偏濃郁重鹹，平時清淡口味者建議請店家加清高湯。
・室內冷氣偏弱，炎夏用餐容易悶熱。

💡 營運洞察與消費規則
・僅收現金；內用每人低消 150 元。
・評論無五星贈品打卡跡象，評分真實可靠。

🤫 內行人的秘密撇步
・建議 11:45 前或 13:30 後前往，免排隊超過 15 分鐘。
・推薦選擇家常粗麵，比細麵更能掛住牛油香氣。

🚗 交通與停車指南
・捷運大安森林公園站 1 號出口步行 6 分鐘；建議停車於建國高架橋下。

🚨 特別點名
・老闆娘點餐步調緊湊，入座前建議先確認好麵型，避免猶豫被催促。
```

*(更多範例請參閱 [examples/sample_report.md](examples/sample_report.md))*

---

## 🛠️ 本機獨立執行 (CLI)

```bash
# 1. 設定 SerpApi API Key（至 https://serpapi.com/dashboard 註冊取得，每月免費 250 次查詢）
export SERPAPI_API_KEY="your_api_key_here"

# 2. 直接執行核心腳本（零依賴，免安裝任何第三方套件）
python3 core/tldreview_tool.py "https://maps.app.goo.gl/sample" --markdown

# 或輸出給其他程式使用的 JSON
python3 core/tldreview_tool.py "https://maps.app.goo.gl/sample"

# （選用）安裝為全域 CLI 指令 `tldreview`
pip install -e .
tldreview "https://maps.app.goo.gl/sample" --markdown
```

---

## 🤝 擴充與貢獻

想為其他 Agent（如 OpenAI Custom GPTs、Discord Bot、Telegram Bot 等）建立 Adapter？  
歡迎閱讀 [CONTRIBUTING.md](CONTRIBUTING.md) 提交 PR！

---

## 📄 License
本專案採用 [MIT License](LICENSE) 開源授權。
