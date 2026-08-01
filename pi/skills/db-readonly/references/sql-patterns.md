# Read-only SQL Patterns

## PostgreSQL

Use server-enforced read-only where possible:

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -c "BEGIN READ ONLY; SELECT current_database(), current_user; COMMIT;"
```

Metadata:

```sql
SELECT table_schema, table_name
FROM information_schema.tables
WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
ORDER BY table_schema, table_name;

SELECT table_schema, table_name, column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
ORDER BY table_schema, table_name, ordinal_position;

SELECT schemaname, tablename, indexname, indexdef
FROM pg_indexes
WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
ORDER BY schemaname, tablename, indexname;
```

Safe sampling/counting:

```sql
SELECT COUNT(*) FROM schema.table;
SELECT * FROM schema.table ORDER BY id DESC LIMIT 20;
EXPLAIN (FORMAT TEXT) SELECT * FROM schema.table WHERE id = 123 LIMIT 1;
```

## MySQL / MariaDB

Metadata:

```sql
SHOW TABLES;
DESCRIBE table_name;
SHOW INDEX FROM table_name;

SELECT table_schema, table_name, table_rows
FROM information_schema.tables
WHERE table_schema NOT IN ('mysql', 'performance_schema', 'information_schema', 'sys')
ORDER BY table_schema, table_name;
```

Read-only transaction if supported:

```sql
SET SESSION TRANSACTION READ ONLY;
START TRANSACTION READ ONLY;
SELECT DATABASE(), USER();
COMMIT;
```

## SQLite

Open read-only where possible:

```bash
sqlite3 'file:database.sqlite?mode=ro' '.tables'
```

Metadata:

```sql
SELECT name, type FROM sqlite_schema WHERE type IN ('table', 'view') ORDER BY type, name;
PRAGMA table_info(table_name); -- metadata only, but the guard blocks PRAGMA by default; use manually with care
SELECT * FROM table_name LIMIT 20;
```

## General safety

- Prefer `COUNT(*)`, `MIN`, `MAX`, and grouped aggregates before wide row dumps.
- Avoid `SELECT *` on sensitive tables unless necessary; select only needed columns.
- Always add `LIMIT` for samples.
- Use `EXPLAIN` for query plans; do not use variants that execute mutations.
