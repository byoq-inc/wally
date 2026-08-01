---
name: subagent-supervisor
description: Launch and supervise long-running or overnight coding subagents so workers stay active and productive for an approved bounded window, recover from completion/failure, safely resolve operational blockers, and produce verifiable status without exceeding safety constraints.
---

# subagent-supervisor

Use this skill when its description matches the user's task. Keep the workflow concise and update it when repeated usage reveals better steps.

## Workflow

1. Define each worker's isolated writable repository/branch, bounded objective, prohibited actions, logs, status artifact, validation expectations, and explicit cutoff before launch.
2. Launch workers in named persistent sessions using the correct PTY mode; never treat nohup or one one-shot invocation as an overnight workflow. Use standalone clones when sandboxed agents must commit, because linked-worktree Git metadata may sit outside the writable sandbox.
3. Launch a deterministic watchdog that checks intended worker and reasoning-supervisor sessions, restarts missing sessions with continuation prompts, logs health, and stops restarting at the cutoff.
4. Launch a dedicated reasoning supervisor whose sole goal is worker productivity: inspect status/logs/git/tooling/disk/process errors, diagnose blockers, apply reversible operational fixes, preserve work before migrations, verify recovery, and escalate only owner-only decisions.
5. Require continuation prompts to inspect and preserve existing work, run validation, commit bounded milestones, update status, and avoid churn when evidence-bound work is complete.
6. Never let either supervisor implement unrelated features, merge, push, deploy, access unapproved secrets/data, mutate cloud/production state, weaken safety boundaries, delete non-disposable work, or extend the approved window.
7. Verify watchdog, reasoning supervisor, and every worker immediately after launch and before reporting them as active. Report exact workers, current blockers, safe fixes, and cutoff.
8. At cutoff, collect commits, status artifacts, validation evidence, remaining blockers, and concise owner questions; clean up only disposable supervisor resources after preserving evidence.

## Self-improvement

- If this skill is repeatedly useful, refine the steps with concrete commands/examples.
- If this skill is noisy or too broad, narrow the description trigger.
