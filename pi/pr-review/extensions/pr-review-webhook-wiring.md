# Pi PR Review — direct webhook/orchestrator wiring

## What is now wired

New orchestrator layer:
- `gastown/pi-tracer/extensions/pr-review-orchestrator.ts`

Smoke entrypoint:
- `gastown/pi-tracer/extensions/pr-review-orchestrator-smoke.ts`

## Purpose

This is the thin compatibility layer that accepts the existing webhook-style envelope and routes it directly into the Pi PR review runtime.

It preserves the current external shape:
- `message`
- `sessionKey`
- optional `idempotencyKey`

## Flow

1. existing repo dispatcher sends webhook envelope
2. orchestrator parses prompt text in `message`
3. parser extracts:
   - repo
   - PR number
   - head ref
   - base ref
   - base/head sha when present
   - pipeline kind (`PR_REVIEW_PIPELINE`, `PR_REVIEW_EVENT`, `PR_FIX_PIPELINE`)
4. orchestrator resolves repo root
5. orchestrator creates session-scoped artifact dir
6. orchestrator invokes `runPrReviewRuntime(...)`
7. runtime boots matching repo profiles, probes URLs, uses browser surface, and writes result JSON

## Implemented files

- `pr-review-orchestrator.ts`
  - webhook envelope type
  - prompt parser
  - runtime handoff
- `pr-review-runtime.ts`
  - runtime/artifact/bootstrap path
- `pr-review-browser-api-runner.ts`
  - Pi browser surface adapter

## Smoke-tested path

Tested against PR #237-style message and real checkout:
- envelope parsing worked
- repo root resolution hook worked
- artifact dir creation worked
- runtime invocation worked
- browser screenshots were produced for successful apps

Artifact output example:
- `/tmp/pi-webhook-review-artifacts/hook-github-pr-example-owner-example-repository-237/example-owner-example-repository-pr-237/result.json`

## Remaining production integration work

Still needed to make this the live path:
1. real webhook server should call `runWebhookPrReview(...)`
2. real repo resolver should map `example-owner/example-repository` to a checkout strategy/root
3. real browser surface object from Pi runtime should be passed in
4. idempotency persistence should wrap this call
5. GitHub writeback should consume the resulting `writeback` payload

## Practical invocation shape

Pseudo-code:

```ts
await runWebhookPrReview(envelope, {
  repoRootResolver: resolveRepoRoot,
  artifactRoot: "/opt/pi-assistant/runtime/artifacts",
  browserSurface: piBrowserSurface,
});
```

That is the direct webhook/orchestrator path Frank asked for.
