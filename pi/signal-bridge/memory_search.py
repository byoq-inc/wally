"""Ephemeral, caller-scoped FTS5 memory retrieval; never indexes other vaults.

The caller supplies authorized documents only. Rebuilding on each query avoids
stale/deleted facts and persistent indexes containing private text.
"""
import re
import sqlite3
import hashlib
import json

DATE_POLICY = """Memory dating policy (effective 2026-09-30): Every new or substantively
updated memory entry must carry [recorded: YYYY-MM-DD] in UTC; updates also carry
[updated: YYYY-MM-DD], preserving the original recorded date. Record event dates
separately when known. Never infer event dates from file modification times or
backdate older undated entries: mark their recorded date unknown. Preserve dated
history and clearly identify superseded assertions. Apply this to curated memory,
summaries, and entity facts (using structured timestamps where supported)."""

# Optional assertions must be explicitly audited by content hash in the private
# INSTRUCTIONS document. Unknown/new/changed assertions are ALWAYS mandatory.
# This is a default-deny policy boundary, never a heading or keyword classifier.
FACT_SCHEMA = re.compile(r'<!-- memory-retrieval-facts-v1\s+(\{.*?\})\s*-->', re.S)


def optional_fact_hashes(instructions):
    matches = FACT_SCHEMA.findall(instructions)
    if not matches:
        return set()
    if len(matches) != 1:
        raise ValueError('ambiguous retrieval policy schema')
    raw = json.loads(matches[0])
    if not isinstance(raw, dict) or set(raw) != {'sha256'} or not isinstance(raw['sha256'], list):
        raise ValueError('invalid retrieval policy schema')
    if len(raw['sha256']) > 4096 or any(not isinstance(h, str) or not re.fullmatch('[0-9a-f]{64}', h) for h in raw['sha256']):
        raise ValueError('invalid optional assertion hashes')
    return set(raw['sha256'])


def assertion_hash(heading, body):
    return hashlib.sha256((heading+'\n'+body).encode()).hexdigest()


def visible_instructions(text):
    optional_fact_hashes(text)  # Validate before removing machine metadata.
    return FACT_SCHEMA.sub('', text).strip()


def retrieval_query(current, quoted=''):
    # Callers may supply only an already-authorized same-conversation quote.
    current = str(current or '')[:4096]
    words = re.findall(r'[^\W_]+', current)
    return current + ('\n' + str(quoted or '')[:2048] if len(words) <= 12 else '')


def chunks(text):
    """Heading-aware paragraphs with source line numbers and bounded sizes."""
    heading = ''
    block = []
    start = 1
    for number, line in enumerate(text.splitlines() + [''], 1):
        if line.startswith('#') or not line.strip() or (line.startswith('- ') and block):
            if block:
                value = '\n'.join(block)
                # Preserve source lines rather than silently splitting assertions.
                yield heading, start, value
                block = []
            if line.startswith('#'):
                heading = line.lstrip('# ').strip()
                continue
        if line.strip():
            if not block:
                start = number
            block.append(line)


def mandatory_policy(text, instructions=''):
    optional = optional_fact_hashes(instructions)
    lines = text.splitlines()
    omitted = set()
    for heading, start, body in chunks(text):
        if assertion_hash(heading, body) in optional:
            omitted.update(range(start - 1, start - 1 + len(body.splitlines())))
    # Headings may themselves contain rules or qualifications. Preserve them,
    # including heading-only documents and full ancestor heading structure.
    return '\n'.join(line for index, line in enumerate(lines) if index not in omitted)


def search(documents, query, limit=8, max_bytes=10000, optional_hashes=None):
    """Return source-linked BM25 hits. FTS syntax is never accepted from queries."""
    tokens = list(dict.fromkeys(token[:64] for token in re.findall(r'[^\W_]+', str(query)[:6144].lower(), re.UNICODE)))[:64]
    if not tokens or max_bytes <= 0 or limit <= 0:
        return ''
    match = ' OR '.join('"' + token + '"' for token in tokens)
    db = sqlite3.connect(':memory:')
    try:
        db.execute('CREATE VIRTUAL TABLE memory USING fts5(source UNINDEXED, line UNINDEXED, heading, body, tokenize="unicode61")')
        for source, text in documents:
            if len(text.encode()) > 1048576:
                raise ValueError('authorized memory document exceeds read budget')
            for heading, line, body in chunks(text):
                if optional_hashes is not None and assertion_hash(heading, body) not in optional_hashes:
                    continue
                db.execute('INSERT INTO memory VALUES (?, ?, ?, ?)', (source, line, heading, body))
        rows = db.execute('SELECT source, line, heading, body FROM memory WHERE memory MATCH ? ORDER BY bm25(memory, 0, 0, 2, 1), rowid LIMIT ?', (match, min(limit, 32)))
        output = []
        remaining = max_bytes
        for source, line, heading, body in rows:
            value = f'\n### {source}:{line} — {heading}\n{body}\n'
            size = len(value.encode())
            if size > remaining:
                continue  # Do not truncate assertions or their qualifications.
            output.append(value)
            remaining -= size
        return ''.join(output)
    finally:
        db.close()
