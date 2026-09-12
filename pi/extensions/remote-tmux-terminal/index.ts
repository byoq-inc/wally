import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { listTmuxSessions, openTmuxTerminal } from "./launch.ts";

export * from "./launch.ts";

function textResult(text: string, details: Record<string, unknown>) {
  return { content: [{ type: "text" as const, text }], details };
}

export default function remoteTmuxTerminal(pi: ExtensionAPI) {
  pi.registerTool({
    name: "open_tmux_terminal",
    label: "Open tmux Session in VNC",
    description: "Use this tool when the user naturally asks to open a named dashboard or terminal session in VNC (for example, 'open session Payments in VNC'). The user does not need command syntax, a slash command, a host, a socket, or a tmux name; pass only their human-friendly session wording.",
    parameters: Type.Object({
      session_query: Type.String({ description: "The human-friendly session name from the user's natural request." }),
    }),
    async execute(_id, params) {
      const { selected } = await openTmuxTerminal(params.session_query);
      return textResult(`Opened ${selected.label} in the VNC desktop.`, { id: selected.id, label: selected.label, state: "live" });
    },
  });

  pi.registerTool({
    name: "list_tmux_sessions",
    label: "List Available VNC Sessions",
    description: "Use only to resolve an ambiguous natural request before calling open_tmux_terminal. Returns safe labels and opaque IDs only; the user never needs slash commands or connection details.",
    parameters: Type.Object({}),
    async execute() {
      const sessions = await listTmuxSessions();
      return textResult(JSON.stringify(sessions), { sessions });
    },
  });
}
