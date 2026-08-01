---
name: db-readonly
description: Safely inspect databases in read-only mode. Use when connecting to PostgreSQL, MySQL/MariaDB, SQLite, or another SQL database to view schemas, sample rows, counts, explain plans, or answer data questions without modifying data. Triggers on requests like "query the DB", "inspect prod database", "readonly database access", "show tables", "check rows", "run SELECT", or "database investigation".
---

# DB Readonly

Use this skill to query databases without changing data or schema.

## Hard rules

- Do not run mutating SQL: `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `UPSERT`, `REPLACE`, `CREATE`, `ALTER`, `DROP`, `TRUNCATE`, `GRANT`, `REVOKE`, `VACUUM`, `ANALYZE`, `CALL`, `EXEC`, `COPY ... FROM`, or DDL/DCL.
- Prefer a database/user/transaction that enforces read-only at the server level.
- Never print credentials, connection strings with passwords, tokens, or `.env` values.
- Add `LIMIT` to exploratory row queries unless the user explicitly asks for full output.
- For production data, minimize returned columns and avoid exposing PII unless required.
- If credentials are needed, use environment variables or 1Password injection; do not store secrets in the skill or repo.

## Workflow

1. Identify database type and connection method from project docs, env names, or user-provided non-secret context.
2. If the connection should use the `AWS_access` 1Password item, load it with `scripts/with_aws_access.py`; never print the concealed credential.
3. Establish read-only protection:
   - PostgreSQL: use `BEGIN READ ONLY;` and/or `default_transaction_read_only=on`.
   - MySQL/MariaDB: use `SET SESSION TRANSACTION READ ONLY; START TRANSACTION READ ONLY;` where supported.
   - SQLite: open with read-only URI mode when possible (`file:path?mode=ro`).
4. Use `scripts/readonly_sql_guard.py` before running any ad-hoc SQL.
5. Start with metadata queries: tables, columns, indexes, row estimates/counts.
6. Run narrow `SELECT`/`WITH`/`EXPLAIN` queries with limits.
7. Summarize findings and include the exact read-only queries used when useful.

## 1Password AWS_access helper

Use `with_aws_access.py` to pull `AWS_access` from 1Password and run a database/AWS client with secret fields in environment variables only. It maps common 1Password field labels to `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `AWS_SESSION_TOKEN` for child commands.

```bash
# Show loaded field names only; values are redacted
/root/.pi/agent/skills/db-readonly/scripts/with_aws_access.py --print-nonsecret

# Verify AWS credentials without printing secrets
/root/.pi/agent/skills/db-readonly/scripts/with_aws_access.py --validate-aws --region us-east-1 -- \
  aws sts get-caller-identity --output json

# PostgreSQL: maps hostname/username/credential to PGHOST/PGUSER/PGPASSWORD
/root/.pi/agent/skills/db-readonly/scripts/with_aws_access.py --pg -- \
  psql -v ON_ERROR_STOP=1 -c "BEGIN READ ONLY; SELECT current_database(), current_user; COMMIT;"

# MySQL/MariaDB: maps credential to MYSQL_PWD and exports AWS_ACCESS_HOSTNAME/USERNAME
/root/.pi/agent/skills/db-readonly/scripts/with_aws_access.py --mysql -- \
  bash -lc 'mysql -h "$AWS_ACCESS_HOSTNAME" -u "$AWS_ACCESS_USERNAME" --batch --execute "SHOW TABLES;"'

# SSH/bastion workflows: materializes credential as a temporary 0600 key file, then deletes it
/root/.pi/agent/skills/db-readonly/scripts/with_aws_access.py --ssh-key-file -- \
  bash -lc 'ssh -i "$AWS_ACCESS_KEY_FILE" -N -L 15432:db.internal:5432 "$AWS_ACCESS_USERNAME@$AWS_ACCESS_HOSTNAME"'
```

If using `op` directly, follow the 1Password skill's tmux requirement. The default item is `op://Automation/AWS_access`; override with `AWS_ACCESS_OP_VAULT` or `AWS_ACCESS_OP_ITEM`.

## Guard script

Use the bundled guard to reject obvious mutating SQL before execution:

```bash
python3 /root/.pi/agent/skills/db-readonly/scripts/readonly_sql_guard.py query.sql
printf 'SELECT * FROM users LIMIT 5;' | python3 /root/.pi/agent/skills/db-readonly/scripts/readonly_sql_guard.py -
```

The guard is a safety net, not a substitute for server-side read-only permissions.

## Query examples

See `references/sql-patterns.md` for PostgreSQL, MySQL, and SQLite read-only query patterns.
