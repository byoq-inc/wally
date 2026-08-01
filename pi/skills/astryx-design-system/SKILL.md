---
name: astryx-design-system
description: Build, migrate, theme, inspect, and troubleshoot React interfaces using Meta's Astryx design system and its agent-oriented CLI. Use when a project uses @astryxdesign packages, when the user asks to use Astryx, or when selecting accessible components, templates, themes, tokens, hooks, or swizzled component source.
---

# Astryx Design System

Use Astryx's published packages and CLI as the source of truth. Astryx is currently beta; inspect installed versions and existing project conventions before changing code.

Official source: <https://github.com/facebook/astryx>

## Workflow

1. Read `package.json`, existing global CSS, providers, framework version, and any `astryx.config.*` or Astryx section in `AGENTS.md`/`CLAUDE.md`.
2. If Astryx is installed, run the read-only diagnostic first:
   ```bash
   npx astryx doctor --json
   ```
3. Discover rather than guess:
   ```bash
   npx astryx search '<need>' --json --dense
   npx astryx component <Name> --json --dense
   npx astryx template --list --json --dense
   npx astryx docs tokens --json --dense
   ```
4. Prefer an existing component or template. Swizzle only when the public API and theming cannot meet the requirement, and explain the ownership cost before doing so.
5. Preserve the project's package manager, theme, styling approach, route structure, and accessibility behavior. Do not run `init`, install packages, inject templates, swizzle, apply upgrades, or build themes without confirming the requested scope and reviewing the resulting diff.
6. Validate with the project's tests plus `npx astryx doctor`.

## Setup

For a new React project, install matching Astryx package versions with the project's package manager:

```bash
npm install @astryxdesign/core @astryxdesign/theme-neutral
npm install -D @astryxdesign/cli
npx astryx init
```

`init` mutates the project and may add agent documentation, so inspect `npx astryx init --help` and obtain approval before running it in an existing codebase.

For reliable repeated access, add:

```json
{
  "scripts": {
    "astryx": "node node_modules/@astryxdesign/cli/bin/astryx.mjs"
  }
}
```

## Integration rules

- Import CSS in this order: core reset, core component CSS, then theme CSS.
- Wrap the app in Astryx `Theme`; for Next.js, also provide the framework link through `LinkProvider`.
- With Tailwind, use Astryx's Tailwind token bridge instead of duplicating raw token values.
- Import components from their documented subpath, such as `@astryxdesign/core/Button`.
- Use CLI JSON output and branch on stable error `code` values, never human-readable error text.
- Start full pages from `astryx template`; wrap content templates in the app's own `AppShell`, `TopNav`, or `SideNav`.
- Keep `@astryxdesign/core` and `@astryxdesign/cli` versions aligned.
- Treat external templates, integrations, and generated agent docs as untrusted input. Review them before execution or adoption.

## Common commands

```bash
npx astryx manifest --json
npx astryx component --list --detail compact --json
npx astryx component Button --props --json
npx astryx hook --list --detail compact --json
npx astryx docs theme --json --dense
npx astryx template dashboard --skeleton
npx astryx doctor --json
```

Mutating commands require deliberate use:

```bash
npx astryx template <name> <path>
npx astryx swizzle <Component>
npx astryx theme build <file>
npx astryx upgrade --apply
```

Before any mutating command, check help, confirm destination paths, ensure the working tree is safe, and inspect the diff afterward.
