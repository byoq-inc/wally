import { execFile } from "node:child_process";
import { promisify } from "node:util";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const execFileAsync = promisify(execFile);

const DEFAULT_ONE_PASSWORD_REF = "op://Automation/Database/credential";
const DEFAULT_TIMEOUT_MS = 15_000;
const MAX_SQL_LENGTH = 20_000;
const MAX_OUTPUT_LENGTH = 60_000;

const FORBIDDEN_SQL = /\b(insert|update|delete|drop|create|alter|truncate|grant|revoke|copy|vacuum|analyze|refresh|call|do|execute|set|reset|begin|start|commit|rollback|savepoint|release|lock|listen|notify)\b/i;
const ALLOWED_START = /^(select|with|explain|show)\b/i;

const DbReadonlyParams = {
	type: "object",
	properties: {
		action: {
			type: "string",
			enum: ["query", "schema", "tables", "explain"],
			description: "Readonly database action to run.",
		},
		sql: { type: "string", description: "Read-only SQL for query/explain actions." },
		limit: { type: "number", description: "Max displayed rows for query actions. Default 100, max 1000." },
	},
	required: ["action"],
	additionalProperties: false,
} as const;

type DbReadonlyAction = "query" | "schema" | "tables" | "explain";

interface DbReadonlyInput {
	action: DbReadonlyAction;
	sql?: string;
	limit?: number;
}

function clampLimit(value: number | undefined): number {
	if (!Number.isFinite(value ?? 100)) return 100;
	return Math.max(1, Math.min(1000, Math.trunc(value ?? 100)));
}

function stripSqlComments(sql: string): string {
	return sql
		.replace(/--.*$/gm, "")
		.replace(/\/\*[\s\S]*?\*\//g, "")
		.trim();
}

function validateReadonlySql(sql: string): string {
	const cleaned = stripSqlComments(sql);
	if (!cleaned) throw new Error("SQL is required.");
	if (cleaned.length > MAX_SQL_LENGTH) throw new Error(`SQL exceeds ${MAX_SQL_LENGTH} characters.`);
	if (cleaned.includes("\\")) throw new Error("psql meta-commands are not allowed.");
	if (!ALLOWED_START.test(cleaned)) throw new Error("Only SELECT, WITH, EXPLAIN, and SHOW statements are allowed.");
	if (FORBIDDEN_SQL.test(cleaned)) throw new Error("SQL contains a forbidden non-read-only keyword.");
	const semicolons = cleaned.split(";").filter((part) => part.trim()).length;
	if (semicolons > 1 || (cleaned.includes(";") && !cleaned.trim().endsWith(";"))) {
		throw new Error("Multiple SQL statements are not allowed.");
	}
	return cleaned.replace(/;\s*$/, "");
}

function redact(value: string): string {
	return value
		.replace(/postgres(?:ql)?:\/\/[^\s@]+@/gi, "postgres://***@")
		.replace(/mysql:\/\/[^\s@]+@/gi, "mysql://***@")
		.replace(/(password|passwd|pwd)=([^\s&]+)/gi, "$1=***");
}

function fieldValue(item: unknown, idOrLabel: string): string {
	const fields = (item as { fields?: Array<{ id?: string; label?: string; value?: string }> }).fields ?? [];
	const field = fields.find((candidate) => candidate.id === idOrLabel || candidate.label === idOrLabel);
	return field?.value?.trim() ?? "";
}

function databaseUrlFromOnePasswordItem(raw: string): string {
	const item = JSON.parse(raw) as unknown;
	const host = fieldValue(item, "hostname") || fieldValue(item, "server");
	const port = fieldValue(item, "port");
	const database = fieldValue(item, "database");
	const username = fieldValue(item, "username");
	const password = fieldValue(item, "password");
	const options = fieldValue(item, "options") || fieldValue(item, "connection options");
	if (!host || !database || !username) throw new Error("Database item is missing host, database, or username.");
	const auth = password ? `${encodeURIComponent(username)}:${encodeURIComponent(password)}` : encodeURIComponent(username);
	const portPart = port ? `:${port}` : "";
	const query = options ? `?${options.replace(/^\?/, "")}` : "";
	return `postgres://${auth}@${host}${portPart}/${encodeURIComponent(database)}${query}`;
}

async function readDatabaseUrl(): Promise<string> {
	if (process.env.DB_READONLY_URL?.trim()) return process.env.DB_READONLY_URL.trim();
	const ref = process.env.DB_READONLY_1PASSWORD_REF?.trim() || DEFAULT_ONE_PASSWORD_REF;
	try {
		const { stdout } = await execFileAsync("op", ["read", ref], { timeout: 10_000, maxBuffer: 1024 * 1024 });
		const value = stdout.trim();
		if (value) return value;
	} catch {
		// Fall back to reading the structured 1Password Database item below.
	}
	try {
		const itemRef = process.env.DB_READONLY_1PASSWORD_ITEM?.trim() || "Database";
		const vault = process.env.DB_READONLY_1PASSWORD_VAULT?.trim() || "Automation";
		const { stdout } = await execFileAsync("op", ["item", "get", itemRef, "--vault", vault, "--format", "json"], {
			timeout: 10_000,
			maxBuffer: 1024 * 1024,
		});
		return databaseUrlFromOnePasswordItem(stdout);
	} catch (error) {
		const message = error instanceof Error ? error.message : String(error);
		throw new Error(`Could not read Database credential from 1Password: ${redact(message)}`);
	}
}

function postgresEnv(databaseUrl: string): { env: NodeJS.ProcessEnv; args: string[] } {
	const env: NodeJS.ProcessEnv = { ...process.env };
	try {
		const parsed = new URL(databaseUrl);
		if (!/^postgres(?:ql)?:$/.test(parsed.protocol)) throw new Error("not a postgres URL");
		env.PGHOST = parsed.hostname;
		if (parsed.port) env.PGPORT = parsed.port;
		env.PGDATABASE = decodeURIComponent(parsed.pathname.replace(/^\//, ""));
		if (parsed.username) env.PGUSER = decodeURIComponent(parsed.username);
		if (parsed.password) env.PGPASSWORD = decodeURIComponent(parsed.password);
		const sslmode = parsed.searchParams.get("sslmode");
		if (sslmode) env.PGSSLMODE = sslmode;
		return { env, args: [] };
	} catch {
		return { env, args: [databaseUrl] };
	}
}

async function runPsql(databaseUrl: string, sql: string, timeoutMs = DEFAULT_TIMEOUT_MS): Promise<string> {
	const connection = postgresEnv(databaseUrl);
	const env = {
		...connection.env,
		PGOPTIONS: `${process.env.PGOPTIONS ?? ""} -c default_transaction_read_only=on -c statement_timeout=${timeoutMs}`.trim(),
	};
	const wrapped = [
		"BEGIN READ ONLY;",
		`SET LOCAL statement_timeout = ${Math.max(1000, timeoutMs)};`,
		`${sql};`,
		"COMMIT;",
	].join("\n");
	try {
		const { stdout, stderr } = await execFileAsync(
			"psql",
			[...connection.args, "-X", "-v", "ON_ERROR_STOP=1", "-P", "pager=off", "-c", wrapped],
			{ env, timeout: timeoutMs + 2000, maxBuffer: 1024 * 1024 * 4 },
		);
		return [stdout, stderr].filter(Boolean).join("\n").trim();
	} catch (error) {
		const message = error instanceof Error ? error.message : String(error);
		throw new Error(redact(message));
	}
}

function truncateOutput(output: string): string {
	if (output.length <= MAX_OUTPUT_LENGTH) return output;
	return `${output.slice(0, MAX_OUTPUT_LENGTH)}\n\n[truncated ${output.length - MAX_OUTPUT_LENGTH} chars]`;
}

function sqlForInput(input: DbReadonlyInput): string {
	if (input.action === "tables") {
		return "SELECT table_schema, table_name, table_type FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog', 'information_schema') ORDER BY table_schema, table_name LIMIT 500";
	}
	if (input.action === "schema") {
		return "SELECT table_schema, table_name, column_name, data_type, is_nullable FROM information_schema.columns WHERE table_schema NOT IN ('pg_catalog', 'information_schema') ORDER BY table_schema, table_name, ordinal_position LIMIT 1000";
	}
	const sql = validateReadonlySql(input.sql ?? "");
	if (input.action === "explain") {
		const withoutExplain = sql.replace(/^explain\b\s*/i, "");
		return `EXPLAIN ${withoutExplain}`;
	}
	const limit = clampLimit(input.limit);
	return `SELECT * FROM (${sql}) AS db_readonly_subquery LIMIT ${limit}`;
}

export default function dbReadonlyExtension(pi: ExtensionAPI): void {
	pi.registerTool({
		name: "db_readonly",
		label: "DB Readonly",
		description: "Run read-only PostgreSQL inspection queries using the Database credential in 1Password. Supports query, explain, tables, and schema actions.",
		promptSnippet: "Use db_readonly for safe database inspection. Never attempt writes; queries are wrapped in a read-only transaction.",
		promptGuidelines: [
			"Use action=tables before querying unfamiliar databases.",
			"Use action=schema to inspect columns.",
			"Only use SELECT/WITH/SHOW/EXPLAIN SQL.",
			"Do not expose credentials or connection strings in responses.",
		],
		parameters: DbReadonlyParams,
		async execute(_toolCallId, params: DbReadonlyInput) {
			const databaseUrl = await readDatabaseUrl();
			const sql = sqlForInput(params);
			const output = await runPsql(databaseUrl, sql);
			return {
				content: [{ type: "text", text: truncateOutput(output) || "No rows." }],
				details: {
					action: params.action,
					readonly: true,
					credentialRef: process.env.DB_READONLY_1PASSWORD_REF?.trim() || DEFAULT_ONE_PASSWORD_REF,
				},
			};
		},
	});

	pi.registerCommand("db-readonly-test", {
		description: "Test the DB readonly 1Password credential and connection",
		handler: async (_args, ctx) => {
			try {
				const databaseUrl = await readDatabaseUrl();
				const output = await runPsql(databaseUrl, "SELECT current_database() AS database, current_user AS user, now() AS checked_at");
				ctx.ui.notify(`DB readonly connection OK:\n${truncateOutput(output)}`, "info");
			} catch (error) {
				const message = error instanceof Error ? error.message : String(error);
				ctx.ui.notify(`DB readonly connection failed: ${redact(message)}`, "error");
			}
		},
	});
}
