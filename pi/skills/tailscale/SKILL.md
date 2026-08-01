---
name: tailscale
description: Manage and troubleshoot Tailscale on this VPS, including tailscale status/whois/ip, Serve/Funnel exposure, tailnet-only access, and OpenClaw Gateway Tailscale setup. Use when asked about Tailscale, tailnet access, MagicDNS, Serve, Funnel, or exposing local services securely.
---

# Tailscale

Use the `tailscale` CLI for tailnet status, identity, Serve/Funnel, and service exposure.

## Safety

- Prefer **Serve** for tailnet-only HTTPS exposure.
- Use **Funnel** only when the user explicitly wants public internet exposure.
- Never expose admin/control services publicly without password/token auth.
- Before changing Serve/Funnel config, inspect current state.
- Do not print auth keys, secrets, or private config.

## Basic checks

```bash
tailscale status
tailscale ip -4
tailscale ip -6
tailscale netcheck
tailscale serve status
tailscale funnel status
```

If not logged in:

```bash
tailscale up
```

For SSH/user identity checks:

```bash
tailscale whois <tailnet-ip>
```

## Serve / Funnel

Tailnet-only HTTPS proxy to a local service:

```bash
tailscale serve --bg http://127.0.0.1:<PORT>
tailscale serve status
```

Public HTTPS exposure; only with explicit user approval and auth on the app:

```bash
tailscale funnel --bg http://127.0.0.1:<PORT>
tailscale funnel status
```

Reset exposure:

```bash
tailscale serve reset
```

## OpenClaw Gateway notes

For OpenClaw Gateway-specific Tailscale Serve/Funnel behavior, read:

`references/openclaw-gateway-tailscale.md`

Key points:

- `serve`: tailnet-only HTTPS via `tailscale serve`, gateway stays loopback.
- `funnel`: public HTTPS via `tailscale funnel`, requires shared password.
- `gateway.bind: "tailnet"`: direct Tailnet IP bind, no Serve/Funnel HTTPS.
- Serve can use Tailscale identity headers for Control UI/WS if explicitly allowed.
- HTTP API endpoints still require token/password auth.

## Workflow

1. Check `tailscale status` and IPs.
2. Check existing Serve/Funnel config.
3. Confirm desired exposure: tailnet-only Serve vs public Funnel vs direct tailnet bind.
4. Apply minimal config/command.
5. Verify URL and auth mode.
6. Report exact reachable URL and how to reset.
