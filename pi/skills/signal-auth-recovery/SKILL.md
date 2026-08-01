---
name: signal-auth-recovery
description: Build and operate model-auth recovery paths that remain available through Signal when the AI/model provider is unavailable.
---

# signal-auth-recovery

Use this skill when its description matches the user's task. Keep the workflow concise and update it when repeated usage reveals better steps.

## Workflow

1. Keep Signal transport and deterministic recovery logic outside the model process.
2. Restrict recovery commands to explicitly allowed direct-message senders; reject groups and untrusted senders.
3. Detect provider authentication failures from runtime errors without exposing tokens or raw secret-bearing errors.
4. Attempt safe credential recovery from an independently maintained credential store using atomic 0600 writes while preserving unrelated providers.
5. Discard cached model processes after credential updates, start a fresh process, and retry the interrupted request once.
6. Provide exact deterministic Signal commands for status and repair that do not invoke an AI model.
7. Run a throttled watchdog for missing or unusable credentials, but do not treat an expired access token with a refresh token as failed until refresh actually fails.
8. If automated repair is impossible, keep Signal online, notify the owner clearly, and never request passwords or raw tokens over chat.
9. Test allowlisting, secret-safe status, atomic sync, one-shot retry, and failure behavior before restarting the bridge.

## Self-improvement

- If this skill is repeatedly useful, refine the steps with concrete commands/examples.
- If this skill is noisy or too broad, narrow the description trigger.
