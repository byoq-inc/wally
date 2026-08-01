# Recurring Nightly Agent Controller

Reboot-resilient, safety-bounded nightly self-improvement for Pi.

## Schedule and workers

- `agent-work-nightly.timer` starts each night at 22:00 America/New_York.
- The deterministic watchdog and separate reasoning supervisor stop at 08:00.
- A persistent timer recovers a missed start after reboot only when local time is inside the approved window.
- An explicit owner-requested early start uses a `-manual-` run ID so workers can verify the authorization while preserving the 08:00 cutoff.
- `nightly-loop` performs conversation-driven usability review first: at nightly start it atomically rotates the private date-stamped Signal user/assistant log, receives every credential-redacted message in that rotated log (never tool payloads or attachments), ranks recurring friction, implements fixture-backed local fixes, runs regressions, and then handles supporting memory quality, research, and proactive questions.
- `security-audit` performs read-only host/security/operations inspection and writes sanitized reports.
- `agent-work-morning-summary.timer` sends a concise deterministic Signal summary at 08:10.

## Safety

Workers use standalone local repositories and local-only commits. They cannot merge, push, deploy, provision, send email, mutate production/cloud/Gmail/Granola, review draft PRs, or access unapproved private data. The bridge stores only accepted Signal user text and successfully sent Pi text; it excludes tool traces, credentials, raw email/transcripts, and attachment bodies and redacts likely credentials at capture. At nightly start the controller rotates the bounded running log under a shared lock into an immutable SHA-256 manifest. The improvement worker may read only its generated `conversation-evidence.md`; it may never open raw Pi sessions. A failed worker, persistence, evidence, or memory check preserves the rotated source for retry. Only a successful worker pass whose claimed work was persisted, curated memory was validated, and explicit manifest-bound success acknowledgement was validated can delete the rotated source. The staged evidence is removed when the worker exits. It may edit only `USER.md`, `MEMORY.md`, and `SKILL_IDEAS.md` outside its repository. Pi canonical session history and curated Hermes memory are never lifecycle deletion targets. Private pre-run snapshots and hashes support recovery. The security worker is host-read-only. Runtime state and logs are mode-restricted beneath Git-ignored controller state, outside Obsidian. The lifecycle refuses more than 32 source files or 16 MiB instead of evicting unreviewed evidence.

## Operations

```bash
systemctl status agent-work-nightly.timer agent-work-morning-summary.timer
systemctl status agent-work-watchdog agent-work-supervisor
systemctl status 'agent-work-worker@nightly-loop' 'agent-work-worker@security-audit'
journalctl -u agent-work-nightly-start.service -u agent-work-watchdog.service --since today
python3 -m unittest discover -s tests -v
# Inspect only shape/mode, never private evidence contents:
stat -c '%a %U:%G %s %n' state/nightly-loop/conversation-evidence.md
# For an explicitly owner-authorized early start, write a private run ID containing
# "-manual-", then start the watchdog and reasoning supervisor; never move the cutoff.
```
