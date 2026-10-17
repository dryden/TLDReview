# Contributing to TL;DReview

**English** | **[繁體中文](CONTRIBUTING.zh-TW.md)**

Contributions to TL;DReview are welcome! Whether you are reporting a bug, improving the review curation algorithm, or adding an adapter for a new AI Agent platform, we'd love your help.

---

## Adding a New Agent Adapter

If you'd like to add support for a new AI Agent or platform (e.g., Coze, Dify, OpenAI Custom GPT Actions, Discord Bot, Telegram Bot, etc.):

1. Create a new subdirectory under `integrations/`, e.g., `integrations/my_agent/`.
2. Include the following core files:
   - `README.md`: Setup instructions and API key / secret configuration for that platform.
   - `setup_prompt.md` or `adapter.py`: The bootstrap prompt or code bridge.
3. Ensure your agent integration adheres to the analysis standards in [`prompts/SYSTEM_PROMPT_EN.md`](prompts/SYSTEM_PROMPT_EN.md) (and [`prompts/SYSTEM_PROMPT.md`](prompts/SYSTEM_PROMPT.md)):
   - Strict Anti-Hallucination (100% evidence-bound)
   - Rating Manipulation Detection (flagging 5-star check-in freebies)
   - Structured 7-Section Report Formatting
4. Open a Pull Request!

---

## Core Rules & Testing
- `core/tldreview_tool.py` **must remain Zero Dependencies** (Python 3 standard library only) so it can run immediately inside restricted cloud sandboxes without internet package installation.
- Run the zero-dependency unit test suite before submitting a PR:
  ```bash
  python3 -m unittest discover -s tests -v
  ```
