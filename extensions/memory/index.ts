import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { StringEnum } from "@mariozechner/pi-ai";
import { Type, type Static } from "typebox";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { atomicWrite, HistorySummaryManager, matchesForgetQuery, recoverActiveMemoryFile } from "./history-summary.ts";
import { registerMemoryLifecycleHooks, writeWithHistoryRetention } from "./lifecycle.ts";
import { ProfileLock } from "./profile-lock.ts";

const MEMORY_ROOT = process.env.PI_MEMORY_DIR || join(process.env.HOME || "/root", ".pi", "agent", "memory");
const HERMES_DIR = process.env.PI_MEMORY_HERMES_DIR || join(MEMORY_ROOT, "hermes");
const USER_FILE = join(HERMES_DIR, "USER.md");
const MEMORY_FILE = join(HERMES_DIR, "MEMORY.md");
const SKILL_IDEAS_FILE = join(HERMES_DIR, "SKILL_IDEAS.md");
const HISTORY_SOURCE_FILE = join(HERMES_DIR, "HISTORY_ELIGIBLE.json");
const HISTORY_SUMMARY_FILE = join(HERMES_DIR, "HISTORY_SUMMARY.md");
const SESSION_FORGET_FILE = join(HERMES_DIR, "SESSION_FORGET_TOMBSTONES.json");
const SESSION_DB_FILE = process.env.PI_SESSION_RECALL_DB || join(HERMES_DIR, "sessions.sqlite");
const PROFILE_LOCK_FILE = process.env.PI_MEMORY_PROFILE_LOCK || join(HERMES_DIR, ".memory-profile.lock");
const profileLock = new ProfileLock(PROFILE_LOCK_FILE);
const historySummary = new HistorySummaryManager({
  sourceFile: HISTORY_SOURCE_FILE,
  summaryFile: HISTORY_SUMMARY_FILE,
  maxSummaryChars: Number(process.env.PI_MEMORY_HISTORY_SUMMARY_MAX_CHARS || 12_000),
  lockFile: PROFILE_LOCK_FILE,
  tombstoneFile: SESSION_FORGET_FILE,
  activeFiles: { user: USER_FILE, memory: MEMORY_FILE },
});
const SKILLS_DIR = process.env.PI_SKILLS_DIR || join(process.env.HOME || "/root", ".pi", "agent", "skills");
const USER_MAX_CHARS = Number(process.env.PI_MEMORY_USER_MAX_CHARS || 12_000);
const MEMORY_MAX_CHARS = Number(process.env.PI_MEMORY_MAX_CHARS || 24_000);
const SESSION_RECALL_SCRIPT = join(dirname(__filename), "session-recall.py");
const execFileAsync = promisify(execFile);

const addSchema = Type.Object({
  text: Type.String({ description: "Durable memory to curate and store. Do not include secrets." }),
  target: Type.Optional(StringEnum(["user", "memory"] as const)),
  reason: Type.Optional(Type.String({ description: "Why this is durable and useful later." })),
});

type AddParams = Static<typeof addSchema>;

const searchSchema = Type.Object({
  query: Type.String({ description: "Search query over curated memory files." }),
  limit: Type.Optional(Type.Number({ description: "Maximum matching bullets; default 8." })),
  target: Type.Optional(StringEnum(["all", "user", "memory"] as const)),
});

type SearchParams = Static<typeof searchSchema>;

const sessionSearchSchema = Type.Object({
  query: Type.String({ description: "Search query over prior Pi session history." }),
  limit: Type.Optional(Type.Number({ description: "Maximum results; default 8." })),
  cwdOnly: Type.Optional(Type.Boolean({ description: "Restrict to the current working directory's sessions." })),
});

type SessionSearchParams = Static<typeof sessionSearchSchema>;

const skillNoteSchema = Type.Object({
  title: Type.String({ description: "Short name for the reusable workflow or skill opportunity." }),
  evidence: Type.String({ description: "What happened that suggests this should become or improve a skill." }),
  proposedSkill: Type.Optional(Type.String({ description: "Optional hyphen-case skill name." })),
});

type SkillNoteParams = Static<typeof skillNoteSchema>;

const skillDraftSchema = Type.Object({
  name: Type.String({ description: "Hyphen-case skill folder/name." }),
  description: Type.String({ description: "Frontmatter description: what it does and when to use it." }),
  steps: Type.Array(Type.String(), { description: "Reusable workflow steps for SKILL.md." }),
  overwrite: Type.Optional(Type.Boolean({ description: "Overwrite existing draft skill if present; default false." })),
});

type SkillDraftParams = Static<typeof skillDraftSchema>;

const forgetSchema = Type.Object({
  query: Type.String({ description: "Exact topic or durable fact to purge from eligible removed history and its derived summary." }),
});

type ForgetParams = Static<typeof forgetSchema>;

type ParsedMemory = { header: string; bullets: string[] };

const DEFAULT_USER = `# User Memory\n\nCurated durable facts/preferences about the user. Keep this short, stable, and non-sensitive.\n\n`;
const DEFAULT_MEMORY = `# Working Memory\n\nCurated durable facts about the environment, projects, workflows, decisions, and recurring procedures. Keep this short, stable, and non-sensitive.\n\n`;

function tokenize(text: string): Set<string> {
  return new Set((text.toLowerCase().match(/[\p{L}\p{N}_'-]+/gu) || []).filter((t) => t.length > 2));
}

function similarity(a: string, b: string): number {
  const A = tokenize(a);
  const B = tokenize(b);
  if (!A.size || !B.size) return 0;
  let inter = 0;
  for (const t of A) if (B.has(t)) inter++;
  return inter / Math.max(A.size, B.size);
}

function hasSecret(text: string): string | undefined {
  const patterns: Array<[RegExp, string]> = [
    [/(?:\bsk-[A-Za-z0-9_\-]{20,}|\bpk-[A-Za-z0-9_\-]{20,}|\brk-[A-Za-z0-9_\-]{20,}|\bpplx-[A-Za-z0-9_\-]{20,}|\bghp_[A-Za-z0-9_]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}|\bxox[baprs]-[A-Za-z0-9_\-]{20,})/i, "API/token-looking value"],
    [/AKIA[0-9A-Z]{16}/, "AWS access key"],
    [/-----BEGIN [A-Z ]*PRIVATE KEY-----/, "private key"],
    [/\b(password|passwd|secret|token|api[_-]?key)\s*[:=]\s*\S+/i, "inline credential"],
  ];
  for (const [re, label] of patterns) if (re.test(text)) return label;
  return undefined;
}

function normalizeBullet(text: string): string {
  return text.replace(/\s+/g, " ").trim().replace(/^[-*]\s*/, "");
}

function parseMemory(raw: string, fallbackHeader: string): ParsedMemory {
  const lines = raw.replace(/\r\n/g, "\n").split("\n");
  const bullets: string[] = [];
  const headerLines: string[] = [];
  for (const line of lines) {
    if (/^[-*]\s+/.test(line)) bullets.push(normalizeBullet(line));
    else if (bullets.length === 0) headerLines.push(line);
  }
  const header = headerLines.join("\n").trim() ? headerLines.join("\n").trim() + "\n\n" : fallbackHeader;
  return { header, bullets: bullets.filter(Boolean) };
}

async function ensureFiles() {
  await mkdir(HERMES_DIR, { recursive: true });
  if (!existsSync(USER_FILE)) await writeFile(USER_FILE, DEFAULT_USER, "utf8");
  if (!existsSync(MEMORY_FILE)) await writeFile(MEMORY_FILE, DEFAULT_MEMORY, "utf8");
  if (!existsSync(SKILL_IDEAS_FILE)) {
    await writeFile(SKILL_IDEAS_FILE, "# Skill Improvement Ideas\n\nCurated observations that may become or improve Pi skills. Review periodically; promote stable repeated workflows into ~/.pi/agent/skills/.\n\n", "utf8");
  }
}

async function readParsed(target: "user" | "memory"): Promise<ParsedMemory> {
  await ensureFiles();
  const file = target === "user" ? USER_FILE : MEMORY_FILE;
  const fallback = target === "user" ? DEFAULT_USER : DEFAULT_MEMORY;
  return parseMemory(await recoverActiveMemoryFile(file, fallback, SESSION_FORGET_FILE, () => profileLock.assertOwned()), fallback);
}

async function recoverProfileMemory(): Promise<void> {
  // The session tombstone is the commit point. Every extension-owned reader runs
  // this idempotent recovery before returning content, so any interrupted forget
  // is suppression-safe immediately and converges all derived/active artifacts.
  await historySummary.recover();
  await readParsed("user");
  await readParsed("memory");
}

async function writeParsed(target: "user" | "memory", parsed: ParsedMemory) {
  const file = target === "user" ? USER_FILE : MEMORY_FILE;
  const max = target === "user" ? USER_MAX_CHARS : MEMORY_MAX_CHARS;
  await writeWithHistoryRetention({
    header: parsed.header,
    bullets: parsed.bullets,
    maxChars: max,
    target,
    commitCuratedEvictions: (target, candidates, postContent, writeActive) =>
      historySummary.commitCuratedEvictions(target, candidates, postContent, writeActive),
    write: async (content) => {
      await atomicWrite(file, content, () => profileLock.assertOwned());
    },
  });
}

async function forgetEverywhere(query: string): Promise<{ purged: number; activePurged: number; sessionPurged: number }> {
  return profileLock.run(async () => {
    // Resolve every pending retention transaction before the session helper
    // enumerates recall surfaces and creates the authoritative tombstone snapshot.
    // Committed evictions are promoted into eligible source, uncommitted intents
    // are discarded, and malformed, divergent, or equal-identity state aborts
    // with pending evidence preserved before acknowledgement.
    let session: any;
    const archived = await historySummary.forget(query, async () => {
      session = await runSessionRecall(["forget", query]);
    });
    let activePurged = 0;
    for (const target of ["user", "memory"] as const) {
      const parsed = await readParsed(target);
      const retained = parsed.bullets.filter((bullet) => !matchesForgetQuery(bullet, query));
      activePurged += parsed.bullets.length - retained.length;
      if (retained.length !== parsed.bullets.length) {
        parsed.bullets = retained;
        await writeParsed(target, parsed);
      }
    }
    return { purged: archived.purged, activePurged, sessionPurged: Number(session.purged || 0) };
  });
}

function chooseTarget(params: AddParams): "user" | "memory" {
  if (params.target) return params.target;
  const text = params.text.toLowerCase();
  if (/\b(i|me|my|user|prefer|preference|call me|i like|i want|my name)\b/.test(text)) return "user";
  return "memory";
}

async function addMemory(params: AddParams) {
  return profileLock.run(async () => {
  const secret = hasSecret(params.text);
  if (secret) throw new Error(`Refusing to store possible secret (${secret}). Store only non-sensitive durable facts.`);

  const target = chooseTarget(params);
  const parsed = await readParsed(target);
  const now = new Date().toISOString().slice(0, 10);
  const candidateCore = normalizeBullet(params.text);
  const candidate = `[${now}] ${candidateCore}${params.reason ? ` (why: ${normalizeBullet(params.reason)})` : ""}`;

  let action: "added" | "deduped" | "replaced" = "added";
  let replaced: string | undefined;
  const next: string[] = [];
  let inserted = false;

  for (const bullet of parsed.bullets) {
    const sim = similarity(candidateCore, bullet);
    if (sim >= 0.9) {
      action = "deduped";
      inserted = true;
      next.push(bullet);
    } else if (sim >= 0.72 && !inserted) {
      action = "replaced";
      replaced = bullet;
      inserted = true;
      // Consolidation rule: keep the newer clearer memory, preserving the latest date/reason.
      next.push(candidate);
    } else {
      next.push(bullet);
    }
  }
  if (!inserted) next.push(candidate);

  parsed.bullets = next;
  await writeParsed(target, parsed);
  return { action, target, file: target === "user" ? USER_FILE : MEMORY_FILE, replaced };
  });
}

async function searchMemory(params: SearchParams) {
  return profileLock.run(async () => {
  await recoverProfileMemory();
  const limit = Math.max(1, Math.min(params.limit || 8, 25));
  const targets: Array<"user" | "memory"> = params.target === "user" ? ["user"] : params.target === "memory" ? ["memory"] : ["user", "memory"];
  const rows: Array<{ target: "user" | "memory"; bullet: string; score: number }> = [];
  for (const target of targets) {
    const parsed = await readParsed(target);
    for (const bullet of parsed.bullets) {
      const score = similarity(params.query, bullet);
      if (score > 0) rows.push({ target, bullet, score });
    }
  }
  return rows.sort((a, b) => b.score - a.score).slice(0, limit);
  });
}

function formatRows(rows: Awaited<ReturnType<typeof searchMemory>>) {
  if (!rows.length) return "No curated memory results.";
  return rows.map((r, i) => `${i + 1}. [${r.target}] score=${r.score.toFixed(3)} ${r.bullet}`).join("\n");
}

async function runSessionRecall(args: string[]) {
  const { stdout } = await execFileAsync("python3", [SESSION_RECALL_SCRIPT, ...args], {
    maxBuffer: 1024 * 1024 * 5,
    timeout: 60_000,
    env: {
      ...process.env,
      PI_MEMORY_HERMES_DIR: HERMES_DIR,
      PI_SESSION_RECALL_DB: SESSION_DB_FILE,
      PI_SESSION_FORGET_FILE: SESSION_FORGET_FILE,
      PI_MEMORY_PROFILE_LOCK: PROFILE_LOCK_FILE,
      PI_MEMORY_PROFILE_LOCK_HELD: profileLock.isHeld() ? "1" : "0",
      PI_MEMORY_PROFILE_LOCK_OWNER: profileLock.ownerIdentity() ?? "",
    },
  });
  return JSON.parse(stdout);
}

async function sessionSearch(params: SessionSearchParams, cwd?: string) {
  return profileLock.run(async () => {
    await recoverProfileMemory();
    const args = ["search", params.query, "--limit", String(params.limit || 8)];
    if (params.cwdOnly && cwd) args.push("--cwd", cwd);
    return runSessionRecall(args);
  });
}

function formatSessionResults(result: any) {
  const rows = Array.isArray(result?.results) ? result.results : [];
  if (!rows.length) return "No session recall results.";
  return rows.map((r: any, i: number) => {
    const loc = `${r.cwd || "unknown cwd"} ${r.timestamp || ""}`.trim();
    const text = String(r.text || "").replace(/\s+/g, " ").slice(0, 700);
    return `${i + 1}. [${r.role}] ${loc}\n${text}\nsource: ${r.session_file}#${r.entry_id}`;
  }).join("\n\n");
}

function slugifySkillName(name: string): string {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 63);
}

async function addSkillIdea(params: SkillNoteParams) {
  const secret = hasSecret(`${params.title}\n${params.evidence}\n${params.proposedSkill || ""}`);
  if (secret) throw new Error(`Refusing to store possible secret in skill idea (${secret}).`);
  await ensureFiles();
  const now = new Date().toISOString().slice(0, 10);
  const existing = await readFile(SKILL_IDEAS_FILE, "utf8");
  const idea = `## ${params.title}\n\n- date: ${now}\n- proposed_skill: ${params.proposedSkill ? slugifySkillName(params.proposedSkill) : ""}\n- evidence: ${normalizeBullet(params.evidence)}\n\n`;
  if (similarity(existing, `${params.title} ${params.evidence}`) > 0.65) {
    return { action: "deduped", file: SKILL_IDEAS_FILE };
  }
  await writeFile(SKILL_IDEAS_FILE, existing.trimEnd() + "\n\n" + idea, "utf8");
  return { action: "added", file: SKILL_IDEAS_FILE };
}

async function listSkillIdeas() {
  await ensureFiles();
  return readFile(SKILL_IDEAS_FILE, "utf8");
}

async function createSkillDraft(params: SkillDraftParams) {
  const secret = hasSecret(`${params.name}\n${params.description}\n${params.steps.join("\n")}`);
  if (secret) throw new Error(`Refusing to create skill draft containing possible secret (${secret}).`);
  const name = slugifySkillName(params.name);
  if (!name) throw new Error("Invalid skill name.");
  const dir = join(SKILLS_DIR, name);
  const file = join(dir, "SKILL.md");
  if (existsSync(file) && !params.overwrite) throw new Error(`Skill already exists: ${file}. Set overwrite=true to replace.`);
  await mkdir(dir, { recursive: true });
  const body = [
    "---",
    `name: ${name}`,
    `description: ${JSON.stringify(params.description).slice(1, -1)}`,
    "---",
    "",
    `# ${name}`,
    "",
    "Use this skill when its description matches the user's task. Keep the workflow concise and update it when repeated usage reveals better steps.",
    "",
    "## Workflow",
    "",
    ...params.steps.map((s, i) => `${i + 1}. ${s.trim()}`),
    "",
    "## Self-improvement",
    "",
    "- If this skill is repeatedly useful, refine the steps with concrete commands/examples.",
    "- If this skill is noisy or too broad, narrow the description trigger.",
  ].join("\n");
  await writeFile(file, body + "\n", "utf8");
  await addSkillIdea({ title: `Created draft skill ${name}`, evidence: `Draft skill created at ${file}`, proposedSkill: name });
  return { name, file };
}

async function renderInjectedMemory(): Promise<string> {
  return profileLock.run(async () => {
  await ensureFiles();
  await recoverProfileMemory();
  const [user, memory] = await Promise.all([readFile(USER_FILE, "utf8"), readFile(MEMORY_FILE, "utf8")]);
  let historySummaryText = "";
  try {
    historySummaryText = (await readFile(HISTORY_SUMMARY_FILE, "utf8")).trim();
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
  }
  const parts = [user.trim(), memory.trim(), historySummaryText].filter((p) => /\n[-*]\s+/.test(p));
  if (!parts.length) return "";
  return `Curated persistent memory (Hermes-style; use as durable context, not as absolute truth):\n\n${parts.join("\n\n")}`;
  });
}

export default function (pi: ExtensionAPI) {
  pi.registerTool({
    name: "memory_add",
    label: "Memory Add",
    description: "Curate and store a durable Hermes-style memory in USER.md or MEMORY.md. Rejects secrets, dedupes, consolidates similar items, and enforces bounded files.",
    promptSnippet: "Store durable curated memory in USER.md or MEMORY.md.",
    promptGuidelines: [
      "Use memory_add only for durable, reusable facts/preferences/procedures that should help future sessions.",
      "Do not use memory_add for secrets, credentials, transient task state, logs, or one-off facts.",
      "Prefer target=user for user preferences/identity and target=memory for environment, project, workflow, or decision memory.",
    ],
    parameters: addSchema,
    async execute(_id, params) {
      const result = await addMemory(params);
      return {
        content: [{ type: "text", text: `Memory ${result.action} in ${result.target}: ${result.file}${result.replaced ? `\nReplaced: ${result.replaced}` : ""}` }],
        details: result,
      };
    },
  });

  pi.registerTool({
    name: "memory_search",
    label: "Memory Search",
    description: "Search curated Hermes-style memory files USER.md and MEMORY.md.",
    promptSnippet: "Search curated persistent memory.",
    parameters: searchSchema,
    async execute(_id, params) {
      const rows = await searchMemory(params);
      return {
        content: [{ type: "text", text: formatRows(rows) }],
        details: { count: rows.length, userFile: USER_FILE, memoryFile: MEMORY_FILE },
      };
    },
  });

  pi.registerTool({
    name: "memory_forget",
    label: "Memory Forget",
    description: "Purge a topic from eligible removed-history source material and regenerate the derived long-term history summary.",
    promptSnippet: "Purge explicitly forgotten history rather than summarizing it.",
    promptGuidelines: [
      "Use memory_forget for explicit user requests to forget, delete, or remove information from memory/history.",
    ],
    parameters: forgetSchema,
    async execute(_id, params: ForgetParams) {
      const result = await forgetEverywhere(params.query);
      return {
        content: [{ type: "text", text: `Purged ${result.purged} eligible, ${result.activePurged} active, and ${result.sessionPurged} indexed session record(s); durable recall suppression recorded; regenerated ${HISTORY_SUMMARY_FILE}. Raw Pi session JSONL was not rewritten.` }],
        details: { ...result, sourceFile: HISTORY_SOURCE_FILE, summaryFile: HISTORY_SUMMARY_FILE },
      };
    },
  });

  pi.registerTool({
    name: "memory_reindex",
    label: "Memory Curate",
    description: "Compatibility no-op for old indexed memory. Hermes-style memory is file-backed and injected directly; this verifies files exist.",
    parameters: Type.Object({}),
    async execute() {
      await profileLock.run(recoverProfileMemory);
      return {
        content: [{ type: "text", text: `Hermes-style memory needs no index. Files:\n${USER_FILE}\n${MEMORY_FILE}` }],
        details: { userFile: USER_FILE, memoryFile: MEMORY_FILE },
      };
    },
  });

  pi.registerTool({
    name: "session_reindex",
    label: "Session Reindex",
    description: "Rebuild SQLite FTS5 recall index from saved Pi JSONL sessions.",
    parameters: Type.Object({}),
    async execute() {
      const result = await profileLock.run(async () => {
        await recoverProfileMemory();
        return runSessionRecall(["reindex"]);
      });
      return {
        content: [{ type: "text", text: `Indexed ${result.chunks} chunks from ${result.sessions} sessions into ${result.db}` }],
        details: result,
      };
    },
  });

  pi.registerTool({
    name: "session_search",
    label: "Session Search",
    description: "Search prior Pi session history using SQLite FTS5 recall.",
    promptSnippet: "Search previous Pi sessions with SQLite FTS5 recall.",
    promptGuidelines: [
      "Use session_search when prior conversations, previous decisions, or older task context may answer the user's question.",
    ],
    parameters: sessionSearchSchema,
    async execute(_id, params, _signal, _onUpdate, ctx) {
      const result = await sessionSearch(params, ctx.cwd);
      return {
        content: [{ type: "text", text: formatSessionResults(result) }],
        details: result,
      };
    },
  });

  pi.registerTool({
    name: "skill_note",
    label: "Skill Note",
    description: "Record an observation that a repeated workflow should become or improve a Pi skill.",
    promptSnippet: "Record reusable workflow observations for skill self-improvement.",
    promptGuidelines: [
      "Use skill_note when a task reveals a reusable workflow, repeated procedure, missing skill, or improvement to an existing skill.",
      "Do not use skill_note for one-off task details or secrets.",
    ],
    parameters: skillNoteSchema,
    async execute(_id, params) {
      const result = await addSkillIdea(params);
      return { content: [{ type: "text", text: `Skill idea ${result.action}: ${result.file}` }], details: result };
    },
  });

  pi.registerTool({
    name: "skill_create_draft",
    label: "Skill Create Draft",
    description: "Create a draft Pi skill from a reusable workflow. Use only when the workflow is stable enough to encode.",
    promptSnippet: "Create draft Pi skills for stable repeated workflows.",
    promptGuidelines: [
      "Use skill_create_draft only for stable reusable workflows with clear triggers and steps.",
      "Prefer skill_note first when the workflow is still tentative.",
    ],
    parameters: skillDraftSchema,
    async execute(_id, params) {
      const result = await createSkillDraft(params);
      return { content: [{ type: "text", text: `Created draft skill ${result.name}: ${result.file}` }], details: result };
    },
  });

  pi.registerTool({
    name: "skill_ideas",
    label: "Skill Ideas",
    description: "List recorded skill self-improvement ideas.",
    parameters: Type.Object({}),
    async execute() {
      const text = await listSkillIdeas();
      return { content: [{ type: "text", text: text.slice(0, 12_000) }], details: { file: SKILL_IDEAS_FILE } };
    },
  });

  pi.registerCommand("memory", {
    description: "Memory commands: add <text>, user <text>, forget <topic>, search <query>, sessions <query>, skill-ideas, reindex-sessions, path",
    handler: async (args, ctx) => {
      const [cmd, ...rest] = args.trim().split(/\s+/);
      const text = rest.join(" ").trim();
      try {
        if (cmd === "add" && text) {
          const result = await addMemory({ text, target: "memory" });
          ctx.ui.notify(`Memory ${result.action}: ${result.file}`, "info");
        } else if (cmd === "user" && text) {
          const result = await addMemory({ text, target: "user" });
          ctx.ui.notify(`User memory ${result.action}: ${result.file}`, "info");
        } else if (cmd === "forget" && text) {
          const result = await forgetEverywhere(text);
          ctx.ui.notify(`Purged ${result.purged} eligible, ${result.activePurged} active, and ${result.sessionPurged} indexed session record(s); durable recall suppression recorded. Raw session JSONL was not rewritten.`, "info");
        } else if (cmd === "search" && text) {
          ctx.ui.notify(formatRows(await searchMemory({ query: text })), "info");
        } else if (cmd === "sessions" && text) {
          ctx.ui.notify(formatSessionResults(await sessionSearch({ query: text, limit: 8 }, ctx.cwd)), "info");
        } else if (cmd === "skill-ideas") {
          ctx.ui.notify((await listSkillIdeas()).slice(0, 12_000), "info");
        } else if (cmd === "reindex-sessions") {
          const result = await profileLock.run(async () => {
            await recoverProfileMemory();
            return runSessionRecall(["reindex"]);
          });
          ctx.ui.notify(`Indexed ${result.chunks} chunks from ${result.sessions} sessions`, "info");
        } else if (cmd === "path") {
          await ensureFiles();
          ctx.ui.notify(`USER.md: ${USER_FILE}\nMEMORY.md: ${MEMORY_FILE}\nHistory source: ${HISTORY_SOURCE_FILE}\nHistory summary: ${HISTORY_SUMMARY_FILE}\nSession forget tombstones: ${SESSION_FORGET_FILE}\nSkill ideas: ${SKILL_IDEAS_FILE}\nSession DB: ${SESSION_DB_FILE}`, "info");
        } else {
          ctx.ui.notify("Usage: /memory add <text> | user <text> | forget <topic> | search <query> | sessions <query> | skill-ideas | reindex-sessions | path", "info");
        }
      } catch (error) {
        ctx.ui.notify(error instanceof Error ? error.message : String(error), "error");
      }
    },
  });

  registerMemoryLifecycleHooks(pi, {
    forgetEverywhere,
    regenerateHistory: () => profileLock.run(async () => {
      await recoverProfileMemory();
      return historySummary.regenerate();
    }),
    renderInjectedMemory,
    sessionSearch: (query, cwd) => sessionSearch({ query, limit: 5, cwdOnly: false }, cwd),
    formatSessionResults,
    withProfileLock: (work) => profileLock.run(work),
    details: {
      userFile: USER_FILE,
      memoryFile: MEMORY_FILE,
      historySourceFile: HISTORY_SOURCE_FILE,
      historySummaryFile: HISTORY_SUMMARY_FILE,
      sessionForgetFile: SESSION_FORGET_FILE,
      skillIdeasFile: SKILL_IDEAS_FILE,
      sessionDb: SESSION_DB_FILE,
    },
  });
}
