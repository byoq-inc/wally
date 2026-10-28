#!/usr/bin/env python3
"""Stage reviewed Python sources only. Never activate services or copy private state."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

BRIDGE = ('durable_lifecycle.py', 'group_vaults.py', 'memory_search.py',
          'signal_current_delivery.py', 'signal_pi_bridge.py', 'signal_pi_relay.py')
ENTITY = ('__init__.py', 'bridge.py', 'candidates.py', 'cli.py', 'errors.py',
          'index.py', 'migration.py', 'schema.py', 'security.py', 'store.py')
CURATOR = ('group_memory_curator.py', 'memory_dates.py')
FILES = tuple(('pi/signal-bridge/'+x, 'bridge/'+x) for x in BRIDGE) + tuple(
    ('components/entity-memory/entity_memory/'+x, 'entity/entity_memory/'+x) for x in ENTITY
) + tuple(('components/memory-curator/'+x, 'curator/'+x) for x in CURATOR)


def stage(repository: Path, destination: Path) -> dict[str, str]:
    if sys.version_info < (3, 10):
        raise RuntimeError('Python 3.10 or later is required')
    repository = repository.resolve(strict=True)
    if not destination.is_absolute() or destination.parent.resolve(strict=True) != destination.parent:
        raise ValueError('destination must have an existing canonical absolute parent')
    payloads = {}
    for source, target in FILES:
        path = repository/source
        if path.resolve(strict=True) != path or not path.is_file():
            raise ValueError('source must be a regular file without symlink components')
        data = path.read_bytes()
        compile(data, source, 'exec')
        payloads[target] = data
    # Exclusive creation prevents overwriting an existing installation or symlink.
    # An interrupted stage intentionally remains incomplete; it is never activated.
    destination.mkdir(mode=0o700, exist_ok=False)
    hashes = {}
    for target, data in payloads.items():
        path = destination/target
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(data)
        path.chmod(0o600)
        hashes[target] = hashlib.sha256(data).hexdigest()
    with (destination/'SOURCE-MANIFEST.json').open('x') as stream:
        json.dump({'schema': 1, 'status': 'source-only-not-activated', 'files': hashes}, stream, indent=2)
        stream.write('\n')
    return hashes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    hashes = stage(Path(__file__).resolve().parents[1], args.output)
    print(f'Staged {len(hashes)} source files. No configuration, credentials, state, or service activation included.')


if __name__ == '__main__':
    main()
