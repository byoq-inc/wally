# Pi Tracer Skeleton Plan

## Why this shape

Pi already exposes a real RPC mode with:
- strict JSONL framing over stdin/stdout
- prompt / steer / follow_up / abort commands
- state queries
- session stats
- a typed TypeScript RPC client in the open-source repo

That means the first tracer skeleton should target Pi RPC mode, not terminal scraping.

## Repo references used

- `packages/coding-agent/docs/rpc.md`
- `packages/coding-agent/src/modes/rpc/rpc-client.ts`
- `packages/coding-agent/src/modes/rpc/rpc-types.ts`
- `packages/coding-agent/src/modes/rpc/jsonl.ts`
- `packages/coding-agent/examples/extensions/structured-output.ts`
- `packages/coding-agent/src/core/agent-session.ts`

## Smallest viable skeleton

### Components
- `PiTracerRuntimeConfig`
- `PiTracerRequest`
- `PiTracerEvent`
- `PiTracerResult`
- `PiRpcRunner`
- `PiTracer`

### Responsibilities

#### PiRpcRunner
- spawn Pi in RPC mode
- write strict JSONL commands to stdin
- parse strict JSONL events/responses from stdout
- expose `prompt`, `abort`, `getState`, `getMessages`, `getSessionStats`

#### PiTracer
- preflight runtime
- create artifact directory
- construct proof-of-work-first prompt envelope
- launch PiRpcRunner
- watch event stream
- detect first proof-of-work block
- parse and validate proof-of-work
- optionally run tracer-owned validation commands
- write artifacts and final result

## Strong recommendation

The first skeleton should not try to parse arbitrary assistant prose.

Instead, it should require either:
- a strict JSON first reply in the assistant text, or
- a Pi extension/tool that returns structured output and terminates

The existing `structured-output.ts` example suggests a very good future move:
- add a tiny custom Pi extension for `proof_of_work`
- optionally add a final `structured_output` or `tracer_result` tool

But for the first skeleton, we can start with strict JSON in assistant text.

## Proposed first file set

- `gastown/pi-tracer/types.ts`
- `gastown/pi-tracer/jsonl.ts`
- `gastown/pi-tracer/pi-rpc-runner.ts`
- `gastown/pi-tracer/proof-of-work.ts`
- `gastown/pi-tracer/tracer.ts`
- `gastown/pi-tracer/index.ts`

## MVP sequence

1. Preflight
2. Spawn `pi --mode rpc`
3. Send one prompt with proof-of-work-first instructions
4. Capture JSONL stream
5. Extract first assistant proof-of-work JSON block
6. Validate it
7. Wait for idle or abort/timeout
8. Run tracer-side validation commands
9. Write result artifacts

## What to postpone

- custom Pi extension for proof_of_work tool
- SDK embedding instead of subprocess RPC
- multi-run orchestration
- session reuse/forking
- advanced steering
- rich UI integration

## Key implementation note

Pi's JSONL framing is LF-only and explicitly warns not to use Node readline.
So the tracer skeleton should either:
- copy Pi's strict JSONL reader approach, or
- reuse equivalent logic carefully

## Next implementation target

Build a tiny TypeScript tracer skeleton around RPC mode first.
