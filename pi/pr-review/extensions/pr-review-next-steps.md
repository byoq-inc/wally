# Pi PR Review Extension — next implemented chunk

## Added in this chunk

Files:
- `gastown/pi-tracer/extensions/pr-review-service-boot.ts`
- `gastown/pi-tracer/extensions/pr-review-curl-probe.ts`

Updated:
- `gastown/pi-tracer/extensions/pr-review-extension.ts`
- `gastown/pi-tracer/extensions/pr-review-types.ts`

## What is now real

### Service boot helper
`pr-review-service-boot.ts`
- spawns a repo profile command in the right cwd
- merges per-profile env
- waits on configured health URLs
- captures stdout/stderr
- tears the process down afterwards

### curl probe helper
`pr-review-curl-probe.ts`
- runs `curl`
- captures status
- reports success/failure per URL

### Extension wiring
`pr-review-extension.ts`
now:
- matches startup profiles
- boots each candidate profile
- waits for readiness
- runs curl probes against health URLs
- records findings/log snippets
- stops services after probing

## Still not done
- persistent checkout management
- browser automation
- VNC fallback
- more precise repo detection
- actual webhook integration into Pi runtime
- smarter port collision handling
- dependent multi-service startup graphs

## Important limitation
This is still an early runner.
It assumes:
- the repo is already checked out locally
- the repo has installable deps already present or otherwise bootable
- health URLs are enough for initial readiness

That’s okay for the next chunk.

## Best next chunk
1. add browser smoke checks
2. add artifact/screenshot output
3. add profile-level dependency support
4. then wire webhook -> extension invocation
