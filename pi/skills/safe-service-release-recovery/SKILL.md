---
name: safe-service-release-recovery
description: Validate, activate, diagnose, and recover multi-component service releases with cold-start readiness, dependency-contract manifests, immutable evidence, scoped rollback, and bounded fixture cleanup. Use for production release gates, systemd-backed service activation, partial or split-generation deployments, warm-state-masked failures, missing runtime dependencies, incident recovery, rollback planning, and post-recovery verification.
---

# Safe Service Release Recovery

Treat a release as reconstructable only when its declared artifacts can start from fresh state and complete a realistic request. Preserve evidence and existing workloads while making the smallest reversible change.

## Establish the evidence model

1. Label every material statement as `fact`, `inference`, or `unknown`.
2. Promote a statement to fact only from directly observed, retained evidence. State what would resolve an unknown; do not turn timing correlation into causation.
3. Before mutation, write a privacy-safe durable ledger of the state whose continuity matters: exact artifact target and hashes, unit state, process identity/start time/restart count, relevant socket metadata, and bounded workload identifiers. Exclude credentials, private content, and raw transcripts.
4. Capture each planned mutation's before-state immediately and verify the evidence file exists, is nonempty, correctly permissioned, and hashed before proceeding.

## Gate the candidate before activation

1. Require a machine-readable dependency-contract manifest for the complete runtime graph, not only application files. Include executables, libraries, unit/socket definitions, reconciler or migration components, declared configuration, ownership/modes, and cross-component compatibility identifiers.
2. Type artifacts by intended scope, such as `full-runtime` and `app-only`; reject an app-only artifact at a full-runtime activation target.
3. Verify manifest completeness and hashes. Mutation-test the gate by removing each required component and require rejection before any live switch.
4. Validate required non-secret configuration and scope identifiers semantically. Fail closed on missing or ambiguous inputs.
5. In an isolated disposable environment, start from stopped processes, absent runtime sockets, empty caches, and a fresh session. Load candidate unit definitions, start solely from candidate artifacts, and run the real protocol plus a realistic brand-new request through its final artifact/result check.
6. Treat warm liveness and existing-session success only as continuity evidence. Never use them as proof that the selected release can reconstruct the service.

## Prepare rollback and activation

1. Acquire a single mutation lease and freeze the reviewed candidate identity.
2. Record the immutable release ID, manifest hash, current target, previous verified target, activation time, scoped commands, readiness result, and rollback command.
3. Prove the rollback generation is independently complete and cold-startable. Keep it immutable and address it by exact generation, never by a mutable alias.
4. Make the smallest atomic activation. Do not kill, signal, replace, or directly launch a systemd manager, including PID 1 or a user manager. Use only exact scoped unit operations such as `systemctl daemon-reload`, `start`, `stop`, `restart`, or `enable` when authorized.
5. Bind failed post-activation cold readiness to a bounded rollback. Re-verify both restored service behavior and preservation of pre-existing workloads against the before-ledger.

## Recover a degraded service

1. Separate transport health, worker health, dependency unit/socket state, identity or authentication proof, and fresh-request completion. Diagnose the first failing stage.
2. Inventory the effective unit configuration and resolved executable paths. Compare live bytes and hashes with complete reviewed generations.
3. If the shared current generation is incomplete and changing it risks healthy workloads, prefer a minimal, documented pin to an immutable verified generation for only the missing dependency. Treat this as temporary split-generation risk, not a completed release.
4. Preserve the original target and record introduced paths so rollback removes only recovery-owned nodes. Never infer restoration from timestamps alone; compare exact targets and contents.
5. Do not claim continuity that lacks a durable before-ledger. Report the narrower proven continuity and mark the rest unknown.

## Bound tests and teardown

1. Split large fault matrices into disjoint, exhaustive shards with explicit selectors and monotonic deadlines.
2. Register fixture roots and process groups at creation. Run cleanup on success, failure, timeout, interruption, and signal.
3. Terminate only fixture-owned process groups after verifying command, parent, cgroup or namespace, and path ownership. Unmount fixture mounts before removing owned trees.
4. Require a post-test leak gate to find zero fixture processes, mounts, sockets, and temporary roots. A passing assertion suite with leaked fixtures is a failure.

## Declare completion

Require all applicable checks:

- Complete contract manifest and hash verification pass.
- Cold start and genuinely fresh end-to-end request pass from candidate artifacts.
- Effective unit and executable generation match the reviewed intent.
- Existing workloads compare cleanly with the durable before-ledger, or unsupported continuity claims are withdrawn.
- Rollback remains exact, scoped, and independently startable.
- Fixture leak gate is empty.
- Final report separates facts, inferences, unknowns, retained evidence, and remaining risks.

Keep temporary generation pins visible as unresolved risk until a complete release passes the same gates and safely retires them.
