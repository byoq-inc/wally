import { defineTool, type ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { Type } from "file:///usr/lib/node_modules/@mariozechner/pi-coding-agent/node_modules/typebox/build/index.mjs";

interface ProofOfWorkDetails {
  executor_run_id: string;
  workstream_id: string;
  branch_or_worktree: string;
  files_inspected: string[];
  scope_assessment: string;
  first_concrete_edit: string;
  validation_command: string;
  status: "active" | "blocked";
}

const proofOfWorkTool = defineTool({
  name: "proof_of_work",
  label: "Proof Of Work",
  description: "Emit structured proof-of-work for the current bounded execution attempt.",
  promptSnippet: "Emit structured proof-of-work before making edits",
  promptGuidelines: [
    "Use proof_of_work as the first required action before editing files.",
    "proof_of_work must contain exact inspected file paths, the first concrete edit, and the validation command.",
    "Do not substitute prose when proof_of_work is required; call the proof_of_work tool.",
  ],
  parameters: Type.Object({
    executor_run_id: Type.String(),
    workstream_id: Type.String(),
    branch_or_worktree: Type.String(),
    files_inspected: Type.Array(Type.String()),
    scope_assessment: Type.String(),
    first_concrete_edit: Type.String(),
    validation_command: Type.String(),
    status: Type.Union([Type.Literal("active"), Type.Literal("blocked")]),
  }),
  async execute(_toolCallId, params: ProofOfWorkDetails) {
    return {
      content: [{ type: "text", text: `Captured proof_of_work for ${params.workstream_id}` }],
      details: params satisfies ProofOfWorkDetails,
    };
  },
});

export default function proofOfWorkExtension(pi: ExtensionAPI) {
  pi.registerTool(proofOfWorkTool);
}
