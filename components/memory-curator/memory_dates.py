"""Writer-owned UTC dating for assertion paragraphs, not event dates.

The model references prior assertion IDs; it cannot supply recording timestamps.
Unknown legacy recording dates remain unknown, including during consolidation.
"""
import hashlib
import re
from datetime import date, datetime, timezone

STAMP = re.compile(r'\[recorded: (unknown|\d{4}-\d{2}-\d{2})\](?: \[updated: (\d{4}-\d{2}-\d{2})\])?')
REF = re.compile(r'\[source: ([0-9a-f]{16}(?:,[0-9a-f]{16})*)\]')


def blocks(text):
    pending = []
    for line in text.splitlines() + ['']:
        if not line.strip() or line.startswith('#') or (line.startswith('- ') and pending):
            if pending:
                yield False, '\n'.join(pending)
                pending = []
            if line.startswith('#'):
                yield True, line
                continue
        if line.strip():
            pending.append(line)


def plain(body):
    return STAMP.sub('', body).strip().replace('-  ', '- ', 1)


def ident(body):
    return hashlib.sha256(body.encode()).hexdigest()[:16]


def stamp(body):
    found = STAMP.search(body)
    if not found:
        return 'unknown', None
    recorded, updated = found.groups()
    for value in (recorded, updated):
        if value and value != 'unknown':
            date.fromisoformat(value)
    return recorded, updated


def annotated(text):
    return '\n\n'.join(body if heading else f'[source: {ident(body)}] {body}'
                       for heading, body in blocks(text))


def enforce(previous, proposed, today=None):
    today = today or datetime.now(timezone.utc).date().isoformat()
    date.fromisoformat(today)
    prior = {ident(body): body for heading, body in blocks(previous) if not heading}
    unchanged = {plain(body): body for body in prior.values()}
    used = set()
    result = []
    for heading, body in blocks(proposed):
        if heading:
            result.append(body)
            continue
        refs = REF.findall(body)
        new = '[new]' in body
        if len(refs) > 1 or (refs and new) or body.count('[new]') > 1:
            raise ValueError('ambiguous assertion lineage')
        clean = REF.sub('', body).replace('[new]', '').strip()
        clean = plain(clean)
        if not clean or clean == '-':
            raise ValueError('empty assertion')
        if refs:
            keys = refs[0].split(',')
            if len(keys) != len(set(keys)) or any(k not in prior or k in used for k in keys):
                raise ValueError('unknown or reused assertion lineage')
            used.update(keys)
            originals = [prior[k] for k in keys]
            dates = [stamp(old)[0] for old in originals]
            recorded = 'unknown' if 'unknown' in dates else min(dates)
            changed = len(originals) != 1 or clean != plain(originals[0])
            updated = today if changed else stamp(originals[0])[1]
        elif clean in unchanged:
            original = unchanged[clean]
            key = ident(original)
            if key in used:
                raise ValueError('reused assertion lineage')
            used.add(key)
            recorded, updated = stamp(original)
        elif new:
            recorded, updated = today, None
        else:
            raise ValueError('new or changed assertion requires explicit lineage')
        marker = f'[recorded: {recorded}]' + (f' [updated: {updated}]' if updated else '')
        result.append(('- ' + marker + ' ' + clean[2:]) if clean.startswith('- ') else marker + ' ' + clean)
    return '\n\n'.join(result) + '\n'


DATING_PROMPT = """Writer dating contract: each assertion paragraph or bullet must start with
[source: ID] referencing its CURRENT assertion, or [new] for a genuinely new assertion.
Keep existing source IDs on substantive rewrites; combine source IDs with commas for
consolidation. Never call a correction a new assertion. Do not reuse a source ID in
multiple assertions. Headings need no marker. The writer owns recorded/updated dates;
keep event dates in assertion text. Legacy recording dates are unknown, never infer
them from event dates. Audit all assertions and the complete summary as before."""
