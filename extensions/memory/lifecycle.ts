import { planBoundedBulletRetention, type CuratedEviction, type CuratedMemoryTarget } from "./history-summary.ts";

export interface LifecyclePi {
  on(event: string, handler: (...args: any[]) => Promise<any>): void;
}

export interface RetentionWriteOptions {
  header: string;
  bullets: string[];
  maxChars: number;
  target: CuratedMemoryTarget;
  commitCuratedEvictions(
    target: CuratedMemoryTarget,
    candidates: CuratedEviction[],
    postContent: string,
    writeActive: (content: string) => Promise<void>,
  ): Promise<unknown>;
  write(content: string): Promise<void>;
}

export async function writeWithHistoryRetention(options: RetentionWriteOptions): Promise<{ kept: string[]; removed: string[] }> {
  const planned = planBoundedBulletRetention(options.header, options.bullets, options.maxChars);
  if (planned.removed.length) {
    await options.commitCuratedEvictions(
      options.target,
      planned.removed.map((text, index) => ({
        sourceId: `${options.target}:${index}:${Buffer.from(text.replace(/\s+/g, " ").trim()).toString("base64url").slice(0, 48)}`,
        text,
      })),
      planned.content,
      options.write,
    );
  } else {
    await options.write(planned.content);
  }
  return { kept: planned.kept, removed: planned.removed };
}

export interface LifecycleDependencies {
  forgetEverywhere(query: string): Promise<unknown>;
  regenerateHistory(): Promise<unknown>;
  renderInjectedMemory(): Promise<string>;
  sessionSearch(query: string, cwd?: string): Promise<any>;
  formatSessionResults(result: any): string;
  details: Record<string, string>;
  withProfileLock?<T>(work: () => Promise<T>): Promise<T>;
}

export function naturalForgetQuery(text: string): string | undefined {
  const trimmed = text.trim();
  const patterns = [
    /^(?:please\s+)?(?:forget|delete|remove)\s+(?:what i (?:said|told you) about\s+)?(.+?)\s+from\s+(?:your\s+)?(?:memory|history)[.!]?$/i,
    /^(?:please\s+)?forget\s+(?:what i (?:said|told you) about\s+)(.+?)[.!]?$/i,
  ];
  for (const pattern of patterns) {
    const match = trimmed.match(pattern);
    if (match?.[1]?.trim()) return match[1].trim();
  }
  return undefined;
}

export function registerMemoryLifecycleHooks(pi: LifecyclePi, dependencies: LifecycleDependencies): void {
  pi.on("input", async (event: any) => {
    const query = naturalForgetQuery(event.text);
    if (query) await dependencies.forgetEverywhere(query);
    return { action: "continue" };
  });

  pi.on("session_before_compact", async () => {
    // Compaction is only a regeneration trigger. Raw message content is never
    // inspected, copied, classified, or passed to durable history.
    await dependencies.regenerateHistory();
  });

  pi.on("before_agent_start", async (event: any, ctx: any) => {
    const build = async () => {
      const parts: string[] = [];
      const curated = await dependencies.renderInjectedMemory();
      if (curated) parts.push(curated);
      if (event.prompt && !event.prompt.startsWith("/memory")) {
        try {
          const recall = await dependencies.sessionSearch(event.prompt, ctx.cwd);
          if (Array.isArray(recall.results) && recall.results.length) {
            parts.push(`Relevant prior session recall (SQLite FTS5; use as context, verify if needed):\n\n${dependencies.formatSessionResults(recall)}`);
          }
        } catch {
          // Recall is opportunistic. Malformed/unavailable tombstones fail closed here:
          // no session content is injected when filtering cannot be established.
        }
      }
      parts.push("Skill self-improvement loop: when a task reveals a reusable workflow, repeated procedure, missing capability, or improvement to an existing skill, use skill_note. When the workflow is stable with clear triggers and steps, use skill_create_draft. Do not store secrets or one-off task noise.");
      return {
        message: {
          customType: "memory-recall",
          display: false,
          content: parts.join("\n\n"),
          details: dependencies.details,
        },
      };
    };
    return dependencies.withProfileLock ? dependencies.withProfileLock(build) : build();
  });
}
