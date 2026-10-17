# 🦊 TL;DReview (Too Long; Didn't Review)

<div align="center">

**English** | **[繁體中文](README.zh-TW.md)**

**Turn messy Google Maps reviews into actionable, anti-hallucination intelligence for any AI Agent.**

🌐 **[Official Website (tldreview.app)](https://tldreview.app/)**

[![Website](https://img.shields.io/badge/Website-tldreview.app-blue?logo=googlechrome&logoColor=white)](https://tldreview.app/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-brightgreen.svg)](https://www.python.org/)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-0-success.svg)](core/tldreview_tool.py)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

</div>

---

## 🦊 What is TL;DReview?

**TL;DReview** ([https://tldreview.app/](https://tldreview.app/)) is a Google Maps review extraction and intelligence engine built for AI Agents and personal assistants. Paste any Google Maps link, and it distills hundreds of noisy, lengthy reviews into a structured, evidence-backed briefing: **Must-Try Highlights, Strengths, Weaknesses & Red Flags, Insider Tips, Operational Rules, and Transit & Parking**—so you know everything that matters before you go.

---

## 🆚 Google Maps Already Has Review Summaries—Why TL;DReview?

Google Maps' built-in AI summary is at most three generic lines long, forcing you to scroll through dozens of raw reviews anyway.

![Google Maps Review Summary vs. TL;DReview Report](docs/images/gmaps-vs-tldreview.png)

TL;DReview filters and organizes reviews into actionable categories—**pros, cons, red flags, and insider tips**—giving you the complete picture at a glance.

Plus, because it runs inside your AI assistant, you can:
- 💬 **Ask Follow-Up Questions**: Ask the AI specific questions (e.g., *"Is it stroller-friendly?"* or *"How spicy is the broth?"*) and get answers grounded directly in the reviews.
- 🎛️ **Customize Output Categories**: Tell your AI to add or adjust report sections to match your personal preferences for every future query.

---

## 🤔 Why Not Just Paste a Google Maps URL Directly into an AI?

A common question is: *"Can't I just paste a Google Maps link into ChatGPT, Claude, or Grok and ask it to summarize the reviews?"*  
In practice, pasting a raw Google Maps URL into a general-purpose AI fails or produces unreliable summaries due to several engineering barriers that LLMs cannot solve on their own:

| Stage | Pasting a Raw URL into a General AI | Using the 🦊 TL;DReview Skill |
| :--- | :--- | :--- |
| **1. URL Resolution** | Short links (`maps.app.goo.gl`) and redirect chains often fail, or the AI grabs the wrong hex ID from nearby landmarks in the URL. | Automatically unwraps short links, Instagram wrappers, and EU consent walls, then isolates the exact target `CID / Place ID`. |
| **2. Reading Reviews** | Google Maps reviews are dynamically rendered via complex JS with strict anti-bot blocks. Built-in AI web fetchers only see an empty HTML shell and **cannot read a single review**. | Connects via structured API to reliably extract full review text, timestamps, helpful vote counts, and 1★–5★ rating distributions. |
| **3. Hallucination Risk** | When the AI can't read the reviews, it quietly falls back to searching outdated food blogs—or **hallucinates dishes and flavors out of thin air** based on the place's name. | **100% Evidence-Bound**: Every claim must come from fetched customer reviews. Sections without direct evidence are omitted entirely. |
| **4. Signal-to-Noise** | Even if you manually copy-paste reviews, the top results are often flooded with one-word spam or outdated 5-year-old comments. | Filters out reviews older than 2 years, prioritizes **Local Guides**, and **forcibly preserves ★1–★3 critical reviews** so red flags aren't buried. |
| **5. Fake 5-Star Radar** | General AIs take high star ratings at face value and can't tell if a 4.8★ rating was bought with freebies. | Combines 1★–5★ distribution data with text scanning to detect **"5-star review for a free drink/dish"** promos and **explicitly warns you in the report**. |
| **6. International Travel** | Searching a restaurant in Japan, Korea, or Europe in your native language yields only a handful of tourist reviews. | Automatically infers the **local official language** from the address to fetch native reviews, and **dynamically merges English reviews** when coverage is thin. |

### 🔧 What Technical Details Does TL;DReview Handle Under the Hood?

1. **🔗 Short URL Unwrapping & Precision ID Disambiguation**
   - Links shared from the Google Maps mobile app are usually short URLs (`maps.app.goo.gl/...`, `share.google/...`), social media redirect wrappers (`l.instagram.com/?u=...`), or regional cookie walls (`consent.google.com`).
   - Even after expanding to a full URL, Google Maps `data=!4m6!3m5...` parameters frequently contain multiple hex identifiers—such as referring landmarks (`!5s0x...`) alongside the actual destination (`!1s0x...`). TL;DReview's parser specifically targets `!1s` CIDs, converts decimal `data_cid` values, and extracts `ChI...` Place IDs or `/g/...` Knowledge Graph IDs so the wrong place is never queried.

2. **🧹 Multi-Stage Review Curation for True Representativeness**
   - Raw Google Maps reviews are noisy—filled with outdated feedback from years ago (often under previous management) and low-effort one-liners.
   - Before feeding data to the LLM, TL;DReview runs a deterministic curation pipeline:
     - **Recency Filter**: Excludes reviews older than **2 years** (automatically padding only if a location has fewer than 10 recent reviews).
     - **Priority Ranking**: Ranks reviews in strict order: **① Recent Local Guide deep-dives ➔ ② Critical ★1–★3 reviews (preventing red flags from being drowned out by 5-star praise) ➔ ③ High-like (`+helpful`) consensus reviews**.
     - **High-Volume Pagination**: Automatically fetches a second page for popular spots (>500 reviews) to broaden sample coverage.

3. **🚨 Anti-Manipulation Radar: Detecting "Check-In for Freebies" Fake 5-Star Reviews**
   - Many restaurants and attractions boast a 4.7★–4.9★ rating that is artificially inflated by *"Leave a 5-star review for a free drink / meat plate / discount"* promotions.
   - TL;DReview defeats rating inflation through a dual defense:
     - **Evidence Preservation**: By forcibly retaining ★1–★3 reviews and injecting the **1★–5★ rating distribution** into the prompt context, the AI can immediately spot bimodal (J-shaped / M-shaped) rating anomalies.
     - **Mandatory Alerting**: Whenever reviews mention check-in freebies or review-for-discount incentives, the system prompt mandates an **explicit warning under `💡 Operational Insights & Rules`**, giving you an honest read on the venue's true quality.

4. **🌍 Automatic Local-Language Routing & Multi-Language Fallback**
   - When researching restaurants abroad (e.g., in Japan, South Korea, Thailand, or France), querying only in your preferred summary language often returns just a few tourist reviews.
   - TL;DReview inspects the venue's address to infer its **local official language** (supporting 40+ countries—e.g., `ja` for Japan, `ko` for Korea, `fr` for France) to pull authentic first-hand local reviews.
   - **Smart Secondary Refetch**: If a venue has plenty of total reviews but fewer than 6 recent Local Guide reviews in the local language, TL;DReview **automatically triggers a secondary fetch in English (`en`)**, deduplicates across languages by `review_id` (so translated duplicates don't waste tokens), and lets the LLM synthesize everything into your preferred output language.

5. **✂️ Token Density Optimization & Smart Sentence Truncation**
   - Dumping hundreds of raw reviews into an LLM wastes tokens and triggers *"Lost in the Middle"* attention degradation.
   - TL;DReview caps context at the highest-density sweet spot (**max 30 curated reviews / 8,000 characters**) and uses **Smart Punctuation Truncation** to clip overly long reviews at natural sentence boundaries (`。！？.!?`), never slicing dish names or prices in half.

---

## ✨ Key Features

1. **🛡️ Strict Anti-Hallucination**: Every pro, con, and recommended dish is 100% grounded in real review excerpts; dimensions without evidence are cleanly omitted.
2. **🔍 Rating Manipulation Detection**: Automatically flags *"5-star review for freebies"* campaigns and skewed rating distributions.
3. **🚀 Zero-Dependency Core**: Built purely with the Python 3 standard library—runs out-of-the-box in any Linux VM, Docker container, or restricted AI sandbox without `pip install`.
4. **🧩 Universal Agent Compatibility**: Ready-to-use adapters for **xAI Grok Bot**, **Meta Muse**, **Claude / Cursor (MCP)**, and **LangChain / CrewAI**.

---

## 🗺️ Architecture & Workflow

```mermaid
graph TD
    User([User sends Google Maps URL]) --> Agent[AI Agent / Grok / Muse / Claude]
    Agent --> Core[core/tldreview_tool.py]
    Core --> ShortURL[Unwrap Short URL & Extract CID]
    Core --> SerpApi[Fetch Local + English Reviews via SerpApi]
    Core --> Filter[Curate Reviews & Compress Token Density]
    Filter --> Prompt[prompts/SYSTEM_PROMPT_EN.md]
    Prompt --> Output[🦊 Actionable Intelligence Report]
```

---

## 🔑 Prerequisites: Get a SerpApi API Key

TL;DReview fetches structured Google Maps reviews via [SerpApi](https://serpapi.com/). Before starting:

1. Sign up at the [SerpApi Dashboard](https://serpapi.com/dashboard).
2. Copy your **API Key** from the dashboard.
3. Set it as the `SERPAPI_API_KEY` environment variable (or save it in a local `.env` file / Agent Secret).

> [!TIP]
> SerpApi's free tier includes **250 free searches per month**, which is plenty for personal use. You can also pass `--save-quota` to use strictly 1 credit per lookup.

---

## ⚡ Quick Start by AI Agent Platform

| Platform / Agent | Integration Method | Highlights | Guide |
| :--- | :--- | :--- | :--- |
| **xAI Grok Bot** | Cloud Computer Attachment | Persistent sandbox + encrypted Secret management | [👉 Grok Setup Guide](integrations/grok/) |
| **Meta Muse** | Linux VM Sandbox | Sentinel network allowlist integration | [👉 Muse Setup Guide](integrations/muse/) |
| **Claude / Cursor** | Model Context Protocol (MCP) | One-step mount into Claude Desktop / Cursor / IDEs | [👉 MCP Setup Guide](integrations/mcp/) |
| **LangChain / CrewAI** | Python `@tool` Decorator | Drop-in tool for multi-agent workflows | [👉 LangChain Guide](integrations/langchain/) |
| **CLI / Local Terminal** | Standalone Script / CLI | Supports Markdown and JSON output with 24h disk cache | [👉 Core CLI Guide](#️-standalone-cli-usage) |

---

## 📊 Sample Output Preview

```text
🦊 TL;DReview Digest: Lao Zhang Beef Noodle House
📍 Address: Sec. 1, Xinsheng S. Rd, Da'an District, Taipei City
🏷️ Type: Noodle Shop | ⭐ 4.3 (Total: 1,820 reviews, Analyzed: 24 key reviews)

---

🍜 Must-Try / Core Highlights
- Braised Half-Tendon Half-Beef Noodles: Tendon is melt-in-your-mouth tender, shank slices are thick and flavorful, and the broth has a rich herbal-beef sweetness (strong consensus).
- House Sichuan Pepper Beef Tallow: Adding half a spoonful dramatically elevates the aroma and depth of the soup.

👍 Strengths & Pros
- Lightning-fast service; bowls arrive within 5 minutes of ordering even during peak hours.
- Wide variety of side dishes—marinated cucumber and braised dried tofu are crowd favorites.

👎 Weaknesses & Red Flags
- Broth leans salty and heavy; diners who prefer lighter flavors should ask for extra clear broth.
- Air conditioning is weak indoors, making it stuffy during summer lunch rushes.

💡 Operational Insights & Rules
- Cash only (no credit cards or mobile payments); minimum spend of NT$150 per person for dine-in.
- No signs of "5-star review for freebies" promotions; rating is organic and trustworthy.

🤫 Insider Tips
- Arrive before 11:45 AM or after 1:30 PM to avoid waiting more than 15 minutes in line.
- Order the wide hand-cut noodles instead of thin noodles—they hold the spicy beef tallow aroma much better.

🚗 Transit & Parking
- 6-minute walk from MRT Daan Park Station (Exit 1); drivers should park under the Jianguo Elevated Road lot as the alley has no street parking.

🚨 Notable Callouts
- The owner takes orders at a brisk pace—decide on your noodle type and side dishes before sitting down to avoid feeling rushed.
```

*(See [examples/sample_report.md](examples/sample_report.md) and [prompts/SYSTEM_PROMPT_EN.md](prompts/SYSTEM_PROMPT_EN.md) for more details.)*

---

## 🛠️ Standalone CLI Usage

```bash
# 1. Set your SerpApi API Key (via export, or copy .env.example to .env)
export SERPAPI_API_KEY="your_api_key_here"

# 2. Run the zero-dependency script directly and output Markdown
python3 core/tldreview_tool.py "https://maps.app.goo.gl/sample" --lang en --markdown

# Advanced options: sort order (newest / lowest / highest) or 1-credit quota-saving mode (--save-quota)
python3 core/tldreview_tool.py "https://maps.app.goo.gl/sample" --lang en --sort-by newest --save-quota --markdown

# Output structured JSON for downstream pipelines
python3 core/tldreview_tool.py "https://maps.app.goo.gl/sample" --lang en

# (Optional) Install globally as the `tldreview` CLI command
pip install -e .
tldreview "https://maps.app.goo.gl/sample" --lang en --markdown
```

---

## 🤝 Contributing

Want to build an adapter for another platform (OpenAI Custom GPTs, Discord, Telegram, Dify, Coze, etc.)?  
Check out [CONTRIBUTING.md](CONTRIBUTING.md) and open a Pull Request!

---

## 📄 License

Licensed under the [MIT License](LICENSE).
