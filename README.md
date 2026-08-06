# Wally Pi Setup

This repository mirrors the non-secret parts of the Pi agent setup from this machine.

Included:

- `skills/` custom Pi skills
- `extensions/` Pi extensions
- `prompts/`, `docs/`, and architecture notes
- PR review/proof-of-work tooling
- GitHub webhook and Signal bridge scaffolding with sanitized example configs
- Curated Hermes memory files that are intended to be non-secret (excluding imported transcripts/indexes)

Excluded intentionally:

- Pi auth material (`auth.json`)
- Browser profiles/cookies/history (`chrome-profile/`)
- Session transcripts (`sessions/`)
- Runtime logs and proof event logs
- SQLite recall indexes
- `node_modules/`, caches, and local binary shims
- Secret-bearing concrete config files; use `config.example.json` templates instead
- Nested webhook workspace checkouts; recreate workspaces locally as needed

## Memory history provenance

Durable deleted-history summaries are sourced only from privacy-curated `USER.md` / `MEMORY.md` bullets after a staged transaction verifies that canonical retention actually committed their eviction. Session compaction may regenerate that summary but never contributes message or transcript prose. See `extensions/memory/README.md` for the exact transaction recovery, provenance, forget, and profile-isolation boundaries.

## Restore notes

Copy this checkout into `~/.pi/agent` or selectively copy subdirectories. Then recreate local-only files such as auth/config secrets from 1Password or the host environment.
