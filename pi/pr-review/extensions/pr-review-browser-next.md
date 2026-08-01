# Pi PR Review Extension — browser chunk

## Added in this chunk

New:
- `gastown/pi-tracer/package.json`
- `gastown/pi-tracer/extensions/pr-review-browser-smoke.ts`

Updated:
- `gastown/pi-tracer/extensions/pr-review-extension.ts`
- `gastown/pi-tracer/index.ts`
- `gastown/pi-tracer/tsconfig.json`

## What changed

### TypeScript wiring
Pi tracer now has:
- local `package.json`
- local `typescript`
- `npm run typecheck`

I also adjusted tsconfig module resolution so the existing codebase typechecks under the current ESM-style imports without forcing a large import-suffix rewrite.

### Browser smoke hook
The PR review extension now supports a browser-smoke phase.
Current shape:
- health URL passes curl probe
- then extension optionally calls `runBrowserSmoke(url, browserRunner)`
- if a browser runner is supplied later, it can return screenshot paths
- if not supplied, the extension records that browser smoke is not wired yet

This means the extension contract is now ready for a real browser backend.

## What is still stubbed
- real browser snapshot/navigation backend
- screenshot artifact persistence strategy
- webhook->extension invocation path
- checkout orchestration
- multi-service startup dependencies
- VNC fallback

## Best next chunk
1. implement a concrete browser runner adapter
2. write screenshots/artifacts to a stable location
3. then wire webhook invocation into Pi
