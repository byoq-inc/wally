import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { StringEnum } from "@earendil-works/pi-ai";
import { Type, type Static } from "typebox";
import { execFile } from "node:child_process";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);
const MANAGED_HELPER = process.env.PI_REPOSITORY_KG_HELPER?.trim() || "/usr/local/bin/repository-kg-managed";
const MAX_OUTPUT = 1024 * 1024;

const linkSchema = Type.Object({
  linkId: Type.String({ description: "Stable bounded link identity." }),
  pointer: Type.String({ description: "Only memory:USER.md#anchor or memory:MEMORY.md#anchor." }),
  scopeNodeId: Type.String({ description: "Existing repository/worktree/project/service graph node." }),
  ownerAuthorized: Type.Boolean({ description: "True only with explicit owner authorization for this link." }),
  authorizationProvenance: Type.String({ description: "Bounded owner:... authorization pointer, never authorization text." }),
  confidence: StringEnum(["low", "medium", "high", "verified"] as const),
  verifiedAt: Type.Optional(Type.String({ description: "Canonical UTC timestamp; defaults to current UTC." })),
  sensitivity: Type.Optional(StringEnum(["normal", "internal", "restricted"] as const)),
});

type LinkParams = Static<typeof linkSchema>;

function canonicalNow(): string {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
}

async function updateGraphLink(params: LinkParams) {
  if (params.ownerAuthorized !== true) {
    throw new Error("Owner authorization is required; no graph update was performed.");
  }
  const args = [
    "memory-link",
    "--id", params.linkId,
    "--pointer", params.pointer,
    "--scope", params.scopeNodeId,
    "--owner-authorized",
    "--authorization-provenance", params.authorizationProvenance,
    "--confidence", params.confidence,
    "--verified-at", params.verifiedAt || canonicalNow(),
    "--sensitivity", params.sensitivity || "restricted",
  ];
  const { stdout } = await execFileAsync(MANAGED_HELPER, args, {
    encoding: "utf8",
    maxBuffer: MAX_OUTPUT,
    timeout: 30_000,
    env: {
      PATH: "/root/.pi/agent/bin:/usr/bin:/bin",
      HOME: process.env.HOME || "/root",
      LC_ALL: "C.UTF-8",
    },
  });
  const result = JSON.parse(stdout);
  return result;
}

export default function (pi: ExtensionAPI) {
  pi.registerTool({
    name: "curated_memory_graph_link",
    label: "Curated Memory Graph Link",
    description: "Create/update/supersede one explicit owner-authorized pointer-only curated-memory graph link. Never accepts or reads memory text.",
    promptSnippet: "Update an authorized pointer-only curated-memory graph link.",
    promptGuidelines: [
      "Use curated_memory_graph_link only when the owner explicitly authorizes the exact pointer metadata and target scope.",
      "Never pass memory text, transcripts, messages, secrets, claims inferred from text, or an auto-selected canonical workspace to curated_memory_graph_link.",
    ],
    parameters: linkSchema,
    async execute(_id, params) {
      const result = await updateGraphLink(params);
      return {
        content: [{ type: "text", text: `Curated-memory graph pointer updated: ${result.node_id}` }],
        details: result,
      };
    },
  });
}
