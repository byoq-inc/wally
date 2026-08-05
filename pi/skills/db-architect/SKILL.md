---
name: db-architect
description: Analyze, audit, and optimize database schemas. Use when reviewing DB models (SQLAlchemy, Prisma, Django, Drizzle, etc.), checking index coverage for access patterns, identifying scalability issues, reviewing migrations, or designing new tables. Triggers on "review schema", "analyze database", "DB audit", "check indexes", "schema review", "is my DB scalable", "missing indexes", "access patterns".
---

# DB Architect

Analyze and optimize database schemas for scalability, correctness, and performance.

## Workflow

1. **Discover schema** — Find ORM models, raw SQL migrations, or schema files in the project
2. **Map access patterns** — Identify how the app queries data (routes, services, repos)
3. **Audit** — Check each table against the checklist below
4. **Report** — Output structured findings with severity and specific fix suggestions

## Audit Checklist

### Indexes
- Every FK column indexed?
- Columns used in WHERE/ORDER BY/JOIN indexed?
- Composite indexes for multi-column queries?
- Partial indexes for status-filtered queries (e.g. `WHERE active = true`)?
- No redundant/duplicate indexes?

### Column Types
- Datetime columns timezone-aware?
- String columns have appropriate max lengths (not unbounded TEXT for short values)?
- Enum/status columns use constrained types (not bare strings)?
- UUIDs stored efficiently (native UUID type vs varchar)?
- Monetary values use integer cents (not floats)?

### Constraints
- NOT NULL where business logic requires it?
- UNIQUE constraints on natural keys?
- FK ON DELETE behavior explicit (CASCADE, SET NULL, RESTRICT)?
- CHECK constraints for valid ranges?

### Relationships
- N+1 risks from lazy-loaded relationships?
- Cascades won't accidentally delete too much?
- Join tables indexed on both sides?

### Scalability
- Tables that grow unboundedly have retention/archival strategy?
- High-write tables avoid unnecessary indexes?
- Hot columns (frequently updated) separated from cold data?
- Partitioning candidates identified for large tables?

### Missing Pieces
- Audit/created_at/updated_at timestamps on all tables?
- Soft delete vs hard delete strategy?
- Optimistic locking (version column) where concurrent updates happen?

## Output Format

```markdown
## Schema Audit Report

### Summary
- Tables reviewed: N
- Critical issues: N
- Warnings: N
- Suggestions: N

### Findings

#### [CRITICAL] Missing index on X.user_id
**Table:** X
**Impact:** Full table scan on every user query
**Fix:** `CREATE INDEX ix_x_user_id ON x(user_id);`

#### [WARNING] ...

#### [SUGGESTION] ...

### Access Pattern Coverage
| Pattern | Query | Indexed? | Notes |
|---------|-------|----------|-------|
| ... | ... | ... | ... |
```

## Tips
- Read the routes/API layer to understand real access patterns, not just the schema in isolation
- Check alembic/migration files for drift between models and actual DB state
- Consider read vs write ratios — OLTP tables need different optimization than analytics tables
- For ORMs, check what queries are actually generated (eager vs lazy loading)
