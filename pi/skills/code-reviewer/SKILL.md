---
name: code-reviewer
description: Review code changes produced by coding agents (Claude Code, Codex, etc.) or humans. Use when reviewing PRs, diffs, or agent output for correctness, style, security, and completeness. Triggers on "review this PR", "review the code", "check the agent's work", "review claude code output", "code review", "audit this diff", "review what was built".
---

# Code Reviewer

Review code changes for correctness, security, style, and completeness. Designed to be the "second pair of eyes" on agent-generated or human-written code.

## Workflow

1. **Get the diff** — `git diff base..head` or read changed files
2. **Understand intent** — Read PR description, commit messages, or task spec
3. **Review** — Apply checklist below to every changed file
4. **Report** — Structured findings with severity, file, line, and suggested fix

## Review Checklist

### Correctness
- Does the code do what the PR/task says it should?
- Edge cases handled (empty inputs, null, zero, negative, unicode)?
- Error handling present and appropriate (not swallowing exceptions)?
- Return types match expectations?
- Async/await used correctly (no missing awaits, no blocking in async)?
- Database transactions committed/rolled back properly?

### Security
- User input validated/sanitized?
- No secrets hardcoded (API keys, passwords)?
- SQL injection risk (raw queries with string interpolation)?
- Auth checks present on protected endpoints?
- No path traversal in file operations?
- Webhook endpoints verify signatures?

### Style & Consistency
- Follows existing project patterns?
- Naming consistent with codebase conventions?
- No dead code, commented-out blocks, or TODO-without-ticket?
- Imports organized?
- Type hints present (for typed languages/projects)?

### Completeness
- Tests added/updated for new behavior?
- Migration included if schema changed?
- Config/env vars documented?
- API changes reflected in docs/types/frontend?

### Performance
- No N+1 queries introduced?
- Unnecessary allocations in hot paths?
- Appropriate use of caching?
- Database indexes exist for new query patterns?

### Agent-Specific Checks
When reviewing coding agent output (Claude Code, Codex, etc.):
- Did the agent follow the task spec or drift?
- Any hallucinated imports/APIs that don't exist?
- Any placeholder/stub code left behind ("TODO: implement")?
- Did it modify files outside the intended scope?
- Are generated tests actually testing the right behavior (not just passing)?

## Output Format

```markdown
## Code Review: [PR title or branch]

### Verdict: APPROVE / REQUEST_CHANGES / COMMENT

### Summary
Brief overall assessment.

### Findings

#### 🔴 [BLOCKER] Description
**File:** path/to/file.py:42
**Issue:** What's wrong
**Fix:** Specific suggestion

#### 🟡 [WARNING] Description
...

#### 🟢 [NIT] Description
...

### What Looks Good
- List things done well (positive reinforcement matters)
```

## Escalation to Sub-agents

For complex reviews, spawn specialized sub-agents rather than doing everything yourself:

- **DB changes detected** (migrations, schema, ORM models) → spawn with `db-architect` skill context for index/constraint/scalability review
- **Security-sensitive changes** (auth, crypto, webhooks, secrets) → spawn a focused security review sub-agent
- **Infrastructure changes** (Dockerfiles, CI, Terraform, k8s) → spawn an infra review sub-agent
- **Frontend changes** (React, CSS, accessibility) → spawn a frontend review sub-agent

Pattern:
1. Do a quick first pass yourself (correctness, style, obvious issues)
2. Identify which specialist areas are touched
3. Spawn sub-agents for those areas in parallel
4. Aggregate findings into a single review report

This keeps the main review fast while getting expert-level depth where it matters.

## Tips
- Always read the full diff, not just individual files — cross-file interactions matter
- Check that new dependencies are actually used and version-pinned
- For stacked PRs, review only the incremental diff (not cumulative)
- When reviewing agent output, compare against the original task prompt to check for drift
- Be specific: "line 42 should handle None" beats "needs better error handling"
