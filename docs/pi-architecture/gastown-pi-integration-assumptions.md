# Gastown → Pi Integration Assumptions

## Purpose

This document defines the smallest credible set of assumptions Gastown should make about Pi.

The goal is to keep the first implementation:
- small
- host-portable
- subscription-compatible
- observable
- hard to over-engineer

## Core Decision

Gastown does not reimplement a coding harness.
Gastown uses Pi as the executor runtime.

Pi owns:
- model/provider/auth access
- tool-calling execution
- session persistence
- repo interaction through Pi tools

Gastown owns:
- workstream decomposition
- executor request shaping
- proof-of-work acceptance
- retry/replace/escalate policy
- completeness accounting
- user-facing observability

## Non-Goals

The first integration does not try to:
- control Pi's internal planner
- modify Pi internals
- scrape the full interactive TUI
- implement multi-host scheduling
- support arbitrary Pi extensions on day one
- solve generalized sub-agent trees inside Pi

## Pi Surface Area Gastown Should Use

Gastown should prefer Pi's machine-facing surfaces in this order:

1. JSON mode
2. RPC mode
3. SDK embedding
4. Interactive terminal mode only as a fallback

Reason:
- JSON/RPC are smaller and more deterministic
- TUI scraping is brittle
- SDK can come later if JSON/RPC are sufficient

## First Implementation Assumption

The first usable version should assume:
- one Gastown workstream launches one Pi executor attempt at a time
- each attempt is bounded to one repo path and one file scope
- Pi runs on the same host as the tracer for MVP
- Pi auth is already configured on that host, or bootstrap is reported explicitly

## Pi Capabilities Gastown Assumes Exist

Gastown assumes Pi can:
- start in a declared working directory
- use read/write/edit/bash-style capabilities
- accept a bounded prompt envelope
- emit machine-readable output or at least a parseable structured first reply
- terminate cleanly or detectably
- persist session artifacts independently if desired

Gastown should not assume:
- built-in sub-agents
- built-in permission gates
- built-in plan mode
- built-in completeness tracking

## Runtime Contract Assumptions

### Launch contract
Gastown assumes Pi can be launched with:
- explicit working directory
- explicit mode selection
- explicit prompt input
- observable stdout/stderr or RPC stream

### Output contract
Gastown assumes Pi can provide:
- one initial proof-of-work response
- subsequent output stream for observation
- a final terminal outcome detectable by process exit and/or structured final reply

### Auth contract
Gastown assumes Pi auth is one of:
- subscription-backed local login
- API-key-backed local configuration
- missing and requiring bootstrap

Gastown must classify these separately.

## Proof-of-Work Assumptions

Gastown should require Pi's first meaningful response to contain:
- exact files inspected
- exact scope assessment
- exact first intended edit
- exact validation command

For the first implementation, this may be self-reported plus tracer-side repo observation.
For later versions, observed evidence should dominate self-report.

## Validation Assumptions

Gastown should not trust Pi's narration that validation succeeded.

For MVP:
- Pi may suggest validation commands
- the tracer should run required validation itself when possible
- validation must be recorded as structured evidence

## Observability Assumptions

Gastown should produce three layers of observability:

### 1. Raw execution evidence
- transcript
- event log
- proof-of-work record
- validation stdout/stderr
- final result object

### 2. Workstream summary
- current state
- last active executor
- retries/replacements
- files changed
- latest validation outcome
- blocker if any

### 3. External tracking surfaces
- Obsidian/GitHub for markdown summaries and architecture docs
- Linear for units of work and status tracking

## Failure Assumptions

Gastown should assume Pi executor attempts can fail in at least these ways:
- Pi missing
- auth missing
- auth expired/restricted
- workspace invalid
- launch failure
- malformed first reply
- no proof-of-work within timeout
- no first edit within timeout
- out-of-scope edits
- validation failure
- blocked due to external dependency

The first implementation must classify these distinctly.

## Replacement Assumptions

Gastown should treat Pi executor attempts as disposable.

That means:
- a failed or weak attempt is not precious
- replacement is cheaper than endless steering
- session continuity matters less than clean evidence

## Minimal State Model

For the first version, Gastown only needs to understand these executor states:
- `created`
- `awaiting_proof_of_work`
- `active`
- `blocked`
- `failed`
- `stalled`
- `completed`
- `replaced`

Do not build a larger state machine until this is working.

## Minimal Artifact Contract

Every Pi executor attempt should leave behind:
- `request.json`
- `proof_of_work.json`
- `events.jsonl`
- `transcript.txt`
- `validation/`
- `result.json`

This is enough for debugging, replacement, and summary generation.

## Minimal Linear Mapping

For the first version:
- one Linear issue = one Gastown workstream
- executor attempts do not become separate Linear issues
- retries/replacements stay in artifacts and summaries, not top-level issue churn

## Minimal Obsidian Mapping

For the first version:
- one markdown note per workstream
- one folder of artifacts per executor attempt
- architecture/spec docs live beside the workstream notes

## What To Avoid

Avoid all of the following in v1:
- sub-agent swarms
- distributed execution
- dynamic extension loading as part of the critical path
- automatic completeness scoring across many workstreams
- building a second coding harness beside Pi
- turning the tracer into a smart planner

## Smallest Real v1

A real v1 is:
- one orchestrator-managed workstream
- one Pi executor attempt at a time
- structured proof-of-work
- tracer-run validation
- structured artifacts
- Linear issue status
- Obsidian summary note

That is enough to prove the control-plane idea.

## Decision Summary

Gastown should treat Pi as:
- executor runtime
- auth/provider layer
- tool-calling engine
- optional session store

Gastown should treat itself as:
- supervisor
- evidence normalizer
- replacement policy owner
- completeness/accountability layer

If this boundary blurs, the system will get too large too fast.
