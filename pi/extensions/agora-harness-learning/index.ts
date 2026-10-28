import { homedir } from "node:os";
import { join } from "node:path";
import { StringEnum, Type } from "@earendil-works/pi-ai";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import {
  defaultStoragePaths,
  loadAgoraConnection,
  postViaRest,
  reportLearning,
  type LearningInput,
} from "./core.js";
import {
  agoraReadsEnabled,
  canonicalCuratedMemoryPaths,
  consumeAgoraContext,
  consumePrefetchedAgoraContext,
  fetchMessagesViaRest,
  prefetchAgora,
  publishCuratedKnowledge,
  runAgoraReadIfEnabled,
  sharingPaths,
  type AgoraMessage,
  type CuratedKnowledgeInput,
} from "./sharing.js";

const learningParameters = Type.Object({
  finding: Type.String({ minLength: 20, maxLength: 280, description: "Novel, reusable Pi or agent-harness finding only" }),
  evidenceKind: StringEnum(["documentation", "measurement", "reproduction", "test"] as const),
  evidence: Type.String({ minLength: 20, maxLength: 480, description: "Concrete verification evidence with no private or project-specific details" }),
  impact: Type.String({ minLength: 20, maxLength: 280, description: "Practical impact for other harness operators" }),
}, { additionalProperties: false });

const curatedMemoryParameters = Type.Object({
  source: StringEnum(["USER.md", "MEMORY.md"] as const, { description: "Curated local memory file that contains this knowledge" }),
  knowledge: Type.String({ minLength: 20, maxLength: 600, description: "Privacy-safe reusable knowledge copied from curated local memory, never from Agora" }),
  usefulness: Type.String({ minLength: 20, maxLength: 280, description: "Why this knowledge is reusable by other agents" }),
}, { additionalProperties: false });

export default function agoraHarnessLearning(pi: ExtensionAPI): void {
  const home = homedir();
  const storage = defaultStoragePaths(home);
  const loadConnection = () => loadAgoraConnection(
    join(home, ".codex", "config.toml"),
    join(home, ".agora", "agora-mcp", "config.json"),
  );
  const memoryPaths = canonicalCuratedMemoryPaths(home);
  let prefetched: Promise<readonly AgoraMessage[] | undefined> = Promise.resolve(undefined);
  let readQueue: Promise<void> = Promise.resolve();

  pi.on("session_start", async () => {
    prefetched = runAgoraReadIfEnabled(process.env, async () => {
      const connection = await loadConnection();
      return prefetchAgora(
        connection,
        sharingPaths(storage, connection.username),
        fetchMessagesViaRest,
        new Date(),
      );
    }).catch(() => undefined);
    await prefetched;
  });

  pi.on("before_agent_start", async (event) => {
    if (!agoraReadsEnabled(process.env)) {
      prefetched = Promise.resolve(undefined);
      return;
    }
    const previous = readQueue;
    let release: () => void = () => undefined;
    readQueue = new Promise<void>((resolve) => { release = resolve; });
    await previous;
    try {
      const connection = await loadConnection();
      const startupMessages = await prefetched;
      prefetched = Promise.resolve(undefined);
      const paths = sharingPaths(storage, connection.username);
      const result = startupMessages === undefined
        ? await consumeAgoraContext(
          connection,
          paths,
          fetchMessagesViaRest,
          event.prompt,
          new Date(),
        )
        : await consumePrefetchedAgoraContext(
          connection,
          paths,
          startupMessages,
          event.prompt,
          new Date(),
        );
      if (!result.context) return;
      return {
        message: {
          customType: "agora-sharing-context",
          content: result.context,
          display: false,
          details: { count: result.deliveredIds.length },
        },
      };
    } catch {
      return;
    } finally {
      release();
    }
  });

  pi.registerTool({
    name: "report_agora_harness_learning",
    label: "Report Agora Harness Learning",
    description: "Publish one verified, novel, reusable Pi/agent-harness learning to Agora general. Input is strictly screened for secrets, private conversation, personal data, and project-specific details; repeats are durably deduplicated.",
    promptSnippet: "Report a verified reusable Pi/agent-harness learning to Agora general",
    promptGuidelines: [
      "Call report_agora_harness_learning only after you personally verified a novel, reusable harness behavior or improvement through a test, reproduction, measurement, or authoritative documentation.",
      "Never pass report_agora_harness_learning secrets, personal data, private conversation content, user requests, file paths, repository/branch/issue names, domains, or other project-specific details.",
      "Do not call report_agora_harness_learning merely because a turn ended; if there is no verified reusable harness learning, do nothing.",
    ],
    parameters: learningParameters,
    async execute(_toolCallId, params, signal) {
      const input: LearningInput = params;
      const result = await reportLearning(input, {
        paths: storage,
        loadConnection,
        post: postViaRest,
        ...(signal ? { signal } : {}),
      });
      const text = result.status === "duplicate"
        ? `Skipped duplicate harness learning (${result.fingerprint.slice(0, 12)}).`
        : `Posted harness learning once as Agora message ${result.id}.`;
      return { content: [{ type: "text", text }], details: result };
    },
  });

  pi.registerTool({
    name: "publish_agora_curated_memory",
    label: "Publish Curated Memory to Agora",
    description: "Publish privacy-safe reusable knowledge explicitly sourced from curated USER.md or MEMORY.md. Local-memory similarity never suppresses a suitable write. Raw Agora feed content is ineligible.",
    promptSnippet: "Share suitable privacy-safe knowledge from curated local memory with other Agora agents",
    promptGuidelines: [
      "Use publish_agora_curated_memory only for reusable knowledge explicitly present in curated USER.md or MEMORY.md; local presence is the required source and is not a duplicate reason.",
      "Never use publish_agora_curated_memory for Agora feed text, session transcripts, user requests, secrets, personal data, or project-specific details.",
      "Never copy Agora sharing context into USER.md or MEMORY.md automatically.",
    ],
    parameters: curatedMemoryParameters,
    async execute(_toolCallId, params, signal) {
      const connection = await loadConnection();
      const input: CuratedKnowledgeInput = params;
      const result = await publishCuratedKnowledge(
        connection,
        sharingPaths(storage, connection.username),
        memoryPaths,
        input,
        postViaRest,
        new Date(),
        signal,
      );
      return {
        content: [{ type: "text", text: `Posted curated reusable knowledge as Agora message ${result.id}.` }],
        details: result,
      };
    },
  });
}
