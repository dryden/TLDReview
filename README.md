# 🦊 TL;DReview (Too Long; Didn't Review)

<div align="center">

**English** | **[繁體中文](README.zh-TW.md)**

**Turn messy Google Maps reviews into actionable, anti-hallucination intelligence for any AI Agent.**

🌐 **[Official Website (tldreview.app)](https://tldreview.app/)**

[![Website](https://img.shields.io/badge/Website-tldreview.app-blue?logo=googlechrome&logoColor=white)](https://tldreview.app/)
[![Version](https://img.shields.io/badge/version-3.0.0-orange.svg)](#-whats-new-changelog)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-brightgreen.svg)](https://www.python.org/)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-0-success.svg)](core/tldreview_tool.py)
[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-tldreview-FFDD00?logo=buymeacoffee&logoColor=black)](https://buymeacoffee.com/tldreview)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

</div>

---

## 🆕 What's New (Changelog)

### 🧪 v3.0.0 (Beta / In Testing)
- 🔒 **Personal Dining Pocket List & Memory Vault (Beta)**: Save restaurants into **📌 Wishlist (`wishlist`)**, **❤️ Liked (`liked`)**, or **💣 Avoid List (`disliked`)** with custom tags and personal notes—and query saved spots near you using **0 SerpApi credits** (see [How to Use the Dining Vault](#-personal-dining-pocket-list-vault--beta) below).

### 🚀 v2.0.0
- 💎 **Zero-Cost Metadata Extraction**: Surfaces **⏳ Popular Times & Wait Time**, **💰 Price Level & Live Open State**, and **📋 Latest Menu Photos** with zero extra credit usage.
- ⚔️ **Multi-Venue Compare (`--compare`) & 🗺️ Area Discovery (`--top 3`)**: Compare 2–3 venues side-by-side in a table, or scan the top 3 nearby spots, strip sponsored ads, and crown the **🏆 True Organic Winner**.
- 🗣️ **Traveler's Point-and-Speak Card**: Cross-language lookups (`ja`, `ko`, `th`, `fr`, etc.) automatically preserve original local-language dish names in parentheses—e.g., `Braised Pork Belly (豚の角煮)`—so you can point at your phone screen to order.

### 🎉 v1.1.0
- 📍 **"Nearby" Coordinate Search & 📸 Signboard/Menu Photo Lookup**: Ask directly for *"FamilyMart near me"* or send a photo of a store signboard/menu (with built-in EXIF GPS extraction).

---

## 🦊 What is TL;DReview?

**TL;DReview** ([https://tldreview.app/](https://tldreview.app/)) is a zero-dependency Google Maps review intelligence engine built for AI Agents. It supports **6 intuitive workflows**:
1. 🔗 **Paste any Google Maps URL** (including short links & redirect wrappers).
2. 📍 **Ask for a "Nearby" Place** (e.g., *"FamilyMart near me"*—locks onto the closest matching branch via GPS coordinates).
3. 📸 **Send a Photo of a Store Signboard or Menu** (extracts store name, address, and EXIF GPS to jump straight into reviews).
4. ⚔️ **Compare Multiple Venues Side-by-Side (`--compare`)** (generates a comparison table of Must-Try, Red Flags, Wait Time, Price, and Best For).
5. 🗺️ **Scan & Audit Top-N Nearby Places (`--top 3`)** (strips sponsored ads, audits fake reviews, and finds the true organic winner nearby).
6. 🔒 **Personal Dining Pocket List Vault (`--vault-save` / `--vault-query`, 🧪 Beta)** (save places to `wishlist`, `liked`, or `disliked` and find nearby saved spots using 0 credits).

---

## 🆚 Why TL;DReview Instead of Google Maps Summaries or Raw AI Links?

Google Maps' built-in summary is at most three generic lines. Meanwhile, pasting a raw Google Maps link directly into ChatGPT, Claude, or Grok fails due to JS anti-bot walls and LLM hallucinations:

| Stage | Pasting a Raw URL into a General AI | Using 🦊 TL;DReview |
| :--- | :--- | :--- |
| **1. Reading Reviews & Anti-Hallucination** | Built-in AI web fetchers only see an empty JS shell, falling back to outdated blogs or **hallucinating dishes out of thin air**. | Extracts verified review text & rating distributions via API. **100% evidence-bound**—sections without evidence are omitted. |
| **2. Signal-to-Noise Curation** | Even copy-pasted reviews are flooded with one-word spam or outdated 5-year-old comments. | Filters out reviews >2 years old and ranks: **① Local Guide deep-dives ➔ ② Critical ★1–★3 reviews ➔ ③ High-like consensus**. |
| **3. Fake 5-Star Radar** | General AIs take 4.8★ ratings at face value and miss review-for-freebie promos. | Scans review text & bimodal 1★/5★ distributions to **explicitly warn you about "5-star review for a free drink/dish" promos**. |
| **4. International Travel Routing** | Querying a restaurant in Japan, Korea, or Europe in English/Chinese misses 90% of local insights. | Infers **local official language (40+ countries)** to fetch native reviews (with English fallback) and builds a **Point-and-Speak ordering card**. |
| **5. Open Status & Menu Price Guardrails** | Outdated review prices or walking to a store only to find it closed or cash-only. | Surfaces **Price, Live Open Status, Popular Times queues**, and **Latest Menu Photos** right in the header. |

---

## 🔒 Personal Dining Pocket List Vault (🧪 Beta)

Ever look up a great restaurant with your AI, only to forget what to order—or which places to avoid—a week later? Now you can ask your AI to save and organize restaurants for you:

### 1️⃣ Three Built-In Status Lists (with Smart Status Upgrades)

| Status (`status`) | Meaning | What You Can Tell Your AI (Examples) |
| :--- | :--- | :--- |
| 📌 **`wishlist` (Want to Go)** | Bookmarked to try later | *"Save this place to my **wishlist**—note: take my parents here next time."* |
| ❤️ **`liked` (Went & Loved It)** | Visited and loved it; save your rating & notes | *"I ate here today and **loved it**! Move it to my liked list, give it 4.8 stars, and note: ask for lighter broth."* |
| 💣 **`disliked` (Avoid List)** | Visited and disliked; never fall for it again | *"I tried this place and it was terrible—put it on my **disliked** list."* |

- **✨ Smart Status Upgrades (Zero Data Loss)**: When you visit a place that was on your **📌 Wishlist (`wishlist`)** and tell your AI *"I went there and loved it (`liked`)!"*, TL;DReview automatically upgrades its status to `liked`, records your `visited_at` date and personal rating, and **preserves all previously saved must-try dishes, insider tips, and notes**!
- **🏷️ Custom Tags (`--tags`)**: Tag any restaurant with custom labels like `date-night`, `family-friendly`, `late-night`, `ramen`, or `taipei-daan`.

### 2️⃣ How to Query Your Saved List (Costs 0 SerpApi Credits!)
Ask your AI anytime—queries run locally with GPS distance calculation and use **0 SerpApi credits**:
- **Find Wishlist Spots Nearby**: *"Is there anything on my wishlist near me right now?"*
- **Revisit Favorites by Tag**: *"Show me places I've `liked` that are tagged `family-friendly`."*
- **Check Your Avoid List**: *"What restaurants are on my `disliked` blacklist?"*

### 3️⃣ Passphrase Escrow, Recovery & Honest Security Model
Because AI Agents often run inside ephemeral cloud VMs (like Meta Muse or Grok) where `~/.tldreview/sync.key` can be wiped when a container resets, v3.0.0 includes **Passphrase Escrow**—inspired by iCloud Keychain Recovery, 1Password Secret Key, and Signal PIN:

- 🗣️ **Recover with One Memorable Sentence (No Raw Keys to Copy)**:
  - **Set a Recovery Passphrase**: Tell your AI *"Set my dining vault recovery passphrase to 'Grandma makes the best braised beef noodles'"* (`--vault-escrow --passphrase "..."`). Your `sync_key` is encrypted client-side using a memory-hard KDF (`hashlib.scrypt`) into a `recovery_blob` stored on the relay.
  - **One-Command Recovery on Any New Device/VM**: Run `--vault-recover --passphrase "Grandma makes the best braised beef noodles"` to fetch and decrypt your `sync_key` and restore your entire restaurant vault!
- 🛡️ **No Silent Key Loss or "Parallel Universe" Vaults**: Non-secret `~/.tldreview/vault_id` is stored separately from `~/.tldreview/sync.key` (`0600`). If `sync.key` is missing or corrupted while `vault_id` exists, TL;DReview **hard-errors and refuses to silently generate a second key**, prompting you to recover via `--vault-recover` or `--vault-import`.
- 🔍 **Honest Threat Model & Durability Contract**:
  - **What E2EE Protects Against**: Even the cloud relay server (Cloudflare Worker / KV) **never sees what restaurants you saved**. All encryption happens client-side before upload.
  - **Agent-as-Custodian Model**: Because the AI Agent decrypts and queries your vault, the Agent's runtime holds the key (`~/.tldreview/sync.key`) and caches plaintext locally at `~/.tldreview/vault.json`. In Docker or ephemeral VMs, mount `~/.tldreview/` as a persistent volume or enable `--vault-escrow`.
  - **Honest Passphrase Tradeoffs**: ① Weak passphrases can be brute-forced offline if the blob is obtained—mitigated via minimum strength checks, memory-hard `scrypt` KDF, and strict server-side rate limiting (max 5 escrow reads/min per IP); ② **Forgetting your passphrase (without a key/JSON backup) means permanent data loss**—there is no support desk that can reset it.
- 📝 **Obsidian Note Export**: Set `TLDREVIEW_OBSIDIAN_DIR=/path/to/obsidian/vault` to automatically export a clean Markdown card for every saved restaurant.

> [!NOTE]
> 🧪 **Beta Notice**: The Personal Dining Vault is currently in **v3.0.0 Beta testing**. Feedback and suggestions are welcome!

---

## 🔑 Prerequisites & Quick Start by Platform

1. Sign up at the [SerpApi Dashboard](https://serpapi.com/dashboard) to get your **API Key** (the free tier includes **250 searches/month**, and querying your Personal Dining Vault uses 0 credits).
2. Set `SERPAPI_API_KEY` in your environment or connect your favorite AI Agent:

| Platform / Agent | Integration Method | Highlights | Guide |
| :--- | :--- | :--- | :--- |
| **xAI Grok Bot** | Cloud Computer Attachment | Persistent sandbox + Secret management | [👉 Grok Setup Guide](integrations/grok/) |
| **Meta Muse** | Linux VM Sandbox | Sentinel network allowlist integration | [👉 Muse Setup Guide](integrations/muse/) |
| **Claude / Cursor** | Model Context Protocol (MCP) | One-step mount into Claude Desktop / Cursor | [👉 MCP Setup Guide](integrations/mcp/) |
| **LangChain / CrewAI** | Python `@tool` Decorator | Drop-in tools for multi-agent workflows | [👉 LangChain Guide](integrations/langchain/) |
| **CLI / Local Terminal** | Zero-Dependency Script | Markdown & JSON output with 24h cache | [👉 See CLI Usage Below](#️-standalone-cli-usage) |

---

## 📊 Sample Output Preview

```text
🦊 TL;DReview Digest: Lao Zhang Beef Noodle House
📍 Address: Sec. 1, Xinsheng S. Rd, Da'an District, Taipei City
🏷️ Type: Noodle Shop | ⭐ 4.3 (Total: 1,820 reviews, Analyzed: 24 key reviews)
💰 Price: $200–400 | 🕒 Status: Open · Closes 9 PM
🛎️ Service & Options: Dine-in | Takeout | Cash only

---

🍜 Must-Try / Core Highlights
- Braised Half-Tendon Half-Beef Noodles: Melt-in-your-mouth tendon, thick shank slices, and rich herbal-beef broth (strong consensus).
- House Sichuan Pepper Beef Tallow: Adding half a spoonful dramatically elevates the aroma.

👍 Strengths & Pros
- Lightning-fast service; bowls arrive within 5 minutes even during peak hours.

👎 Weaknesses & Red Flags
- Broth leans salty; ask for extra clear broth if you prefer lighter flavors. Weak AC in summer.

💡 Operational Insights & Rules
- Cash only; NT$150 minimum spend per person. No signs of fake-review promotions.

🤫 Insider Tips
- Peak hours are Sat 12:00–14:00; arrive before 11:45 AM to skip the line. Choose wide hand-cut noodles.

🚗 Transit & Parking
- 6-minute walk from MRT Daan Park Station (Exit 1); park under the Jianguo Elevated Road lot.

---
💡 Want to save this place to your `wishlist` or mark it as `liked` / `disliked` in your Personal Dining Vault? Just let me know!
```

*(See [examples/sample_report.md](examples/sample_report.md) for more examples.)*

---

## 🛠️ Standalone CLI Usage

```bash
export SERPAPI_API_KEY="your_api_key_here"

# 1. Google Maps URL / "Nearby" GPS Search / Signboard or Menu Photo
python3 core/tldreview_tool.py "https://maps.app.goo.gl/sample" --lang en --markdown
python3 core/tldreview_tool.py "FamilyMart near me" --lat 25.0330 --lng 121.5654 --lang en --markdown
python3 core/tldreview_tool.py --image ./signboard.jpg --lang en --markdown

# 2. Multi-Venue Compare (--compare) / Area Discovery Top-3 Audit (--top 3)
python3 core/tldreview_tool.py "Lin Dong Fang Beef Noodle" "Yong Kang Beef Noodle" --compare --lang en --markdown
python3 core/tldreview_tool.py "ramen near me" --lat 25.0330 --lng 121.5654 --top 3 --lang en --markdown

# 3. Personal Dining Vault: Save to Wishlist (--vault-save)
python3 core/tldreview_tool.py --vault-save \
  --name "Lao Zhang Beef Noodle" --lat 25.0330 --lng 121.5654 \
  --status wishlist --tags "beef-noodle,daan,family-friendly" \
  --must-try "Half-Tendon Half-Beef Noodles,Sichuan Pepper Beef Tallow" \
  --user-notes "Take parents next time, ask for lighter broth" --markdown

# 4. Personal Dining Vault: Upgrade from 'wishlist' to 'liked' after visiting
python3 core/tldreview_tool.py --vault-save \
  --name "Lao Zhang Beef Noodle" --status liked --user-rating 4.8 \
  --user-notes "Parents loved the tendon noodles!" --markdown

# 5. Query Your Vault by Status, Tag, or GPS Radius (0 SerpApi credits!)
python3 core/tldreview_tool.py --vault-query --status wishlist --lat 25.0335 --lng 121.5640 --radius-km 3.0 --markdown
python3 core/tldreview_tool.py --vault-query --status liked --tag "family-friendly" --markdown

# 6. Passphrase Escrow (--vault-escrow) & One-Command Recovery (--vault-recover)
python3 core/tldreview_tool.py --vault-escrow --passphrase "Grandma makes the best braised beef noodles" --markdown
python3 core/tldreview_tool.py --vault-recover --passphrase "Grandma makes the best braised beef noodles" --markdown

# 7. Vault Diagnostics (--vault-status) & Portable JSON Backup (--vault-export / --vault-import)
python3 core/tldreview_tool.py --vault-status --markdown
python3 core/tldreview_tool.py --vault-export ./my_dining_backup.json
python3 core/tldreview_tool.py --vault-import ./my_dining_backup.json --markdown
```

---

## 🤝 Contributing & Sponsor

- **Contribute an Adapter**: Check out [CONTRIBUTING.md](CONTRIBUTING.md) and open a Pull Request!
- **Buy Us a Coffee**: If **TL;DReview** saves you time and helps you dodge tourist traps, consider supporting ongoing development:  
  👉 **[Buy Me a Coffee (https://buymeacoffee.com/tldreview)](https://buymeacoffee.com/tldreview)**

[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-Support%20TL%3BDReview-FFDD00?style=for-the-badge&logo=buymeacoffee&logoColor=black)](https://buymeacoffee.com/tldreview)

---

## 📄 License

Licensed under the [MIT License](LICENSE).
