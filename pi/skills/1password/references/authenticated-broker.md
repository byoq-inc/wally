# Authenticated 1Password broker

## Health and diagnosis

```bash
test -S /run/op-auth-broker/broker.sock
/usr/local/bin/op-auth-client status
```

A healthy broker and an unauthenticated direct `op` CLI can coexist. The broker process may have inherited service-account authentication while the current shell has none. Diagnose the path used by the consuming helper before attempting sign-in.

Status may report cache names, timestamps, counters, circuit state, and discovery counts. It must never expose secret values.

## Named values

```bash
exec 3>destination
/usr/local/bin/op-auth-client get \
  --caller approved-caller \
  --name approved-secret \
  --fd 3
exec 3>&-
```

The caller and secret must already be authorized by the root-owned allowlist.

## Item fields

```bash
exec 3>destination
/usr/local/bin/op-auth-client resolve \
  --caller approved-discovery-caller \
  --item 'Item title' \
  --field credential \
  --fd 3
exec 3>&-
```

Use ordinary item and field names. Paths, `op://` references, option-like names, and controls must fail closed.

## Attachments

```bash
exec 3>destination
/usr/local/bin/op-auth-client resolve-attachment \
  --caller approved-attachment-caller \
  --item 'Key item' \
  --index 0 \
  --fd 3
exec 3>&-
```

Attachment callers must be explicitly allowlisted. Indexes must be bounded. Multiline values such as PEM files remain in broker memory and the destination chosen by the caller. Prefer pinning a stable attachment identity when supported; index selection depends on file ordering.

## Safe configuration changes

1. Preserve the current config and installed-file hashes in a root-only rollback directory.
2. Build a root-owned mode `0600` staging config.
3. Validate the full config with isolated tests.
4. Atomically replace the config.
5. Reload in process with `op-auth-broker-start --reload`.
6. Verify status and canary one authorized read through a sink.

Do not restart merely to apply an allowlist change.

## Restart safety

Before any unavoidable restart, prove that the launcher can discover an approved authenticated donor without exposing its token. Validate root ownership, executable path, supervisor ancestry, command line, and exact service cgroup. Do not assume the donor is a direct child of PID 1; a root systemd user unit may be the approved supervisor.

If donor preflight is unavailable or ambiguous, do not stop the current broker. A copied old binary is not a usable rollback when both versions depend on the same unavailable donor.

## Provider canary

A broker read alone is insufficient. Validate the complete consuming path without printing credentials:

1. Retrieve the field or attachment through the broker.
2. Run the consuming authentication helper.
3. Mint a short-lived provider token when applicable.
4. Perform the narrow intended API or Git operation.
5. Verify the resulting remote object or head identifier.
