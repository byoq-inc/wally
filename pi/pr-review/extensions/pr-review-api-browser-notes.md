# Pi PR Review Extension — API browser surface adapter

## Decision

Use the **API browser surface** as the browser backend for PR smoke validation.

Do not add a separate Playwright sidecar inside this extension layer.

## Added

- `gastown/pi-tracer/extensions/pr-review-browser-api-runner.ts`

## What it does

It adapts an abstract browser API surface into the existing browser smoke runner contract.

Expected surface methods:
- `navigate({ url })`
- `snapshot({ outputPath? })`

Adapter factory:
- `createApiBrowserSmokeRunner({ surface, screenshotDir?, filePrefix? })`

Result:
- the PR review extension can keep calling `runBrowserSmoke(...)`
- the caller can now pass a real API browser surface adapter-backed runner
- screenshot paths can be generated deterministically under a provided artifact dir

## Expected integration shape

Webhook/orchestrator layer should do something like:

```ts
const browserRunner = createApiBrowserSmokeRunner({
  surface: browserSurface,
  screenshotDir: artifactDir,
  filePrefix: `pr-${prNumber}`,
});

await runPrReviewExtension(input, repoRoot, browserRunner);
```

## Why this is the right shape

This keeps:
- browser execution outside the extension core
- extension logic testable
- runtime-specific browser plumbing in one adapter

It also means Pi can swap browser implementations later without rewriting PR review logic.

## Still missing

- real Pi runtime object that exposes the browser API surface to this tracer/extension path
- screenshot artifact persistence conventions at orchestration level
- richer browser actions beyond navigate+snapshot
