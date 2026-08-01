---
name: web-search
description: Search the live web from Pi using the Brave Search API, with the Brave API key loaded safely from 1Password. Use when asked to research current information, look up docs/news/facts, compare sources, or answer questions that need internet/web search.
---

# Web Search

Use the bundled script for live web search:

```bash
/root/.pi/agent/skills/web-search/scripts/brave_search.py "search query" --count 5
```

The script prints concise JSON with `title`, `url`, and `description` for each result.

## Authentication

The script never prints the API key. Auth resolution order:

1. Use `BRAVE_API_KEY` if already present in the environment.
2. Otherwise read from 1Password using `op read "$BRAVE_API_KEY_OP_REF"`.
3. Default 1Password ref: `op://Automation/BRAVE_API_KEY/credential`.

If 1Password access fails, follow the `1password` skill: use `op` inside tmux, verify `op whoami`, and do not expose secrets in logs or chat.

## Common commands

```bash
# Basic search
/root/.pi/agent/skills/web-search/scripts/brave_search.py "pi coding agent extensions" --count 5

# Recent-ish search using Brave freshness filters
/root/.pi/agent/skills/web-search/scripts/brave_search.py "Anthropic latest model news" --freshness week --count 5

# Country/language scoped search
/root/.pi/agent/skills/web-search/scripts/brave_search.py "Bundesliga Tabelle" --country DE --language de --count 5

# Full Brave response when needed
/root/.pi/agent/skills/web-search/scripts/brave_search.py "Brave Search API docs" --raw --count 3
```

## Workflow

1. Run `brave_search.py` with a targeted query.
2. Inspect result titles/snippets/URLs.
3. If the user needs sourced detail, fetch or inspect relevant URLs with available shell tools (`python`, `curl`) while avoiding private/internal URLs and secrets.
4. Cite URLs in the answer when using web-derived facts.

## Notes

- Prefer several focused searches over one broad query.
- Do not include the Brave API key in commands, files, output, or chat.
- See `references/openclaw-web-notes.md` for the OpenClaw behavior this skill mirrors.
