# Pi PR Review Extension Skeleton

This is the initial code skeleton for a Pi-native PR review extension.

## What exists now

Files:
- `gastown/pi-tracer/extensions/pr-review-types.ts`
- `gastown/pi-tracer/extensions/pr-review-repo-profiles.ts`
- `gastown/pi-tracer/extensions/pr-review-extension.ts`

## Current behavior

The skeleton currently does these pieces:
- defines a typed extension input/output contract
- defines repo-specific startup profiles for `example-owner/example-repository`
- inspects git changed paths
- picks matching startup profiles based on touched paths
- infers rough suggested checks
- returns a structured validation plan/result

## Important design choice

Yes: **service boot is repo-specific**.

That means startup should be driven by:
- a repo profile registry first
- heuristics second

The current profile registry lives in:
- `gastown/pi-tracer/extensions/pr-review-repo-profiles.ts`

## What is still stubbed / not implemented yet

Not built yet:
- actual checkout orchestration
- real process boot and cleanup
- readiness waiting
- curl execution
- browser automation execution
- VNC fallback execution
- GitHub writeback integration
- webhook receiver wiring into Pi runtime

## Recommended next implementation order

1. add a real `service_boot` helper that:
   - spawns profile commands
   - tracks pid/process group
   - waits for health URL readiness
   - tears down cleanly
2. add `curl` probing for health/page checks
3. add browser smoke checks
4. wire the extension into the webhook path
5. only then add VNC fallback

## Why this split is right

A single generic boot path will be too brittle.
Different repos need different:
- working directories
- ports
- env vars
- startup commands
- dependent services

So the right model is:
- repo-specific profiles
- shared boot machinery

That gives us both consistency and flexibility.
