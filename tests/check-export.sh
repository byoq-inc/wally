#!/usr/bin/env bash
set -euo pipefail

repo_root=$(git rev-parse --show-toplevel)
cd "$repo_root"
export PYTHONPYCACHEPREFIX="${TMPDIR:-/tmp}/wally-pycache"

./scripts/validate-export.sh

required=(
  .env.example README.md
  config/pi-settings.example.json config/github-webhook.config.example.json
  config/signal-bridge.config.example.json config/op-auth-allowlist.example.json
  pi/extensions/entity-memory/index.ts pi/extensions/bash-timeout/index.ts
  pi/extensions/remote-tmux-terminal/index.ts pi/extensions/remote-tmux-terminal/README.md
  pi/extensions/repository-knowledge-graph/index.ts
  pi/skills/astryx-design-system/SKILL.md pi/skills/gh-issues/SKILL.md
  pi/skills/hallmark/SKILL.md pi/skills/safe-service-release-recovery/SKILL.md
  pi/skills/signal-group-creator/SKILL.md pi/signal-bridge/signal_pi_bridge.py
  pi/github-webhook.py pi/pr-review/package.json operations/agent-work/watchdog.sh
  operations/systemd/pi-entity-memory-nightly.service
  components/op-auth-broker/op-auth-broker components/op-auth-broker/op-auth-client
  components/op-auth-broker/op-auth-broker-start scripts/bootstrap.sh
  scripts/restore.sh scripts/rollback.sh
)
for path in "${required[@]}"; do test -f "$path"; done
for path in components/op-auth-broker/* scripts/*.sh tests/*.sh; do test -x "$path"; done

test ! -e MANIFEST.md
test ! -e inventory.json
test ! -d docs
test ! -d operations/govfiles
test ! -e pi/proof-of-work/events.jsonl
test ! -e pi/extensions/goal.ts
test -z "$(find pi -type f -name '*.skill' -print)"
test ! -e pi/signal-bridge/config.json
test ! -e pi/signal-bridge/thread-state.json
test ! -e pi/signal-bridge/pm-session.json
test -z "$(find . -path ./.git -prune -o -type l -print)"

for path in config/*.json pi/pr-review/package.json pi/pr-review/tsconfig.json; do
  python3 -m json.tool "$path" >/dev/null
done

python3 -m py_compile \
  components/op-auth-broker/op-auth-broker \
  components/op-auth-broker/op-auth-client \
  pi/github-webhook.py pi/github-review/*.py pi/signal-bridge/*.py \
  operations/agent-work/*.py pi/extensions/memory/session-recall.py \
  pi/skills/db-readonly/scripts/*.py pi/skills/web-search/scripts/*.py \
  pi/skills/signal-group-creator/scripts/*.py

mapfile -t shell_files < <(find components operations scripts tests pi/skills -type f \
  \( -name '*.sh' -o -path '*/op-auth-broker-start' \) -print | sort)
bash -n "${shell_files[@]}"
shellcheck "${shell_files[@]}"
./scripts/bootstrap.sh --check

python3 - <<'PY'
import re
from pathlib import Path, PurePosixPath

errors = []
tracked = {PurePosixPath(p) for p in __import__('subprocess').check_output(
    ['git', 'ls-files'], text=True).splitlines()}

# Every retained non-top-level Markdown file must be an agent entrypoint,
# an extension-local README, or be explicitly named by one. No detached human
# documentation tree is allowed.
skills = sorted(Path('pi/skills').glob('*/SKILL.md'))
extension_docs = sorted(Path('pi/extensions').glob('*/README.md'))
allowed_markdown = {
    PurePosixPath('README.md'),
    PurePosixPath('infra/azure/README.md'),  # Reviewed provisioning/migration runbook.
    *(PurePosixPath(p) for p in skills),
    *(PurePosixPath(p) for p in extension_docs),
}
for skill in skills:
    text = skill.read_text()
    # A skill may explicitly route an agent to a topic/language subtree using
    # a placeholder (for example, references/{your_language}/...).  In that
    # case the subtree is agent-consumed dispatch data, not standalone docs.
    routed_reference_tree = 'references/{' in text
    if routed_reference_tree:
        allowed_markdown.update(
            PurePosixPath(path) for path in skill.parent.glob('references/**/*.md')
        )
    for raw in re.findall(r'(?:references|scripts)/[A-Za-z0-9_./-]+', text):
        raw = raw.rstrip('.,):`')
        candidate = PurePosixPath(skill.parent, raw)
        if candidate in tracked:
            if candidate.suffix.lower() in {'.md', '.markdown'}:
                allowed_markdown.add(candidate)
        elif '.' in PurePosixPath(raw).name:
            errors.append(f'{skill}: missing dependency {raw}')
for path in tracked:
    if path.suffix.lower() in {'.md', '.markdown'} and path not in allowed_markdown:
        errors.append(f'{path}: Markdown is not an agent-consumed skill dependency')

# Check ordinary repository-local Markdown links and file:// references.
sources = [Path('README.md'), Path('infra/azure/README.md'), *skills, *extension_docs]
for source in sources:
    try:
        text = source.read_text()
    except UnicodeDecodeError:
        continue
    refs = re.findall(r'\[[^]]*\]\(([^)#]+)', text)
    refs += re.findall(r'file://(/[^\s]+)', text)
    for ref in refs:
        if '://' in ref or ref.startswith('/'):
            continue
        candidate = (source.parent / ref).resolve()
        try:
            rel = candidate.relative_to(Path.cwd().resolve())
        except ValueError:
            continue
        if not candidate.exists():
            errors.append(f'{source}: broken local reference {rel}')

if errors:
    raise SystemExit('\n'.join(sorted(set(errors))))
print(f'Reference closure passed: {len(tracked)} files, {len(skills)} skills')
PY

echo "Operational utility checks passed"
