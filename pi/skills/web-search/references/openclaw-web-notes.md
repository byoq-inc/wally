# OpenClaw web-search notes

OpenClaw's web tool supports multiple providers. This Pi skill intentionally implements the Brave provider path first because the API key exists in 1Password.

Provider behavior mirrored here:

- Provider: Brave Search API
- Key env var: `BRAVE_API_KEY`
- 1Password fallback: `op://Automation/BRAVE_API_KEY/credential`
- Endpoint: `https://api.search.brave.com/res/v1/web/search`
- Header: `X-Subscription-Token: <key>`
- Useful parameters: `q`, `count`, `country`, `search_lang`, `ui_lang`, `freshness`

OpenClaw also has web_fetch, Gemini/Grok/Kimi/Perplexity providers, caching, SSRF guards, and readability extraction. Add those later only if needed; keep this skill focused on search.
