import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { openTmuxTerminal, validateSession, validateTarget } from "./launch.ts";

export * from "./launch.ts";

function textResult(text: string, details: Record<string, unknown>) {
  return { content: [{ type: "text" as const, text }], details };
}

export default function remoteTmuxTerminal(pi: ExtensionAPI) {
  pi.registerTool({
    name: "open_tmux_terminal",
    label: "Open Remote tmux Terminal",
    description: "Open an XFCE terminal on the VPS VNC desktop and attach SSH to a named remote tmux session. Uses existing SSH config, agent, and keys.",
    parameters: Type.Object({
      target: Type.String({ description: "SSH config alias or user@host." }),
      session: Type.String({ description: "Remote tmux session name." }),
    }),
    async execute(_id, params) {
      openTmuxTerminal(params.target, params.session);
      const target = validateTarget(params.target);
      const session = validateSession(params.session);
      return textResult(`Opened VNC terminal for ${target}, tmux session ${session}.`, { target, session });
    },
  });

  pi.registerCommand("tmux-window", {
    description: "Open VNC terminal: /tmux-window <ssh-target> <tmux-session>",
    handler: async (args, ctx) => {
      try {
        const parts = args.trim() ? args.trim().split(/\s+/) : [];
        if (parts.length > 2) throw new Error("Usage: /tmux-window <ssh-target> <tmux-session>");
        let [target, session] = parts;
        if ((!target || !session) && ctx.mode === "tui") {
          target ||= (await ctx.ui.input("SSH target", "user@host or SSH config alias"))?.trim();
          session ||= (await ctx.ui.input("tmux session", "session name"))?.trim();
        }
        if (!target || !session) throw new Error("Both SSH target and tmux session are required. Usage: /tmux-window <ssh-target> <tmux-session>");
        openTmuxTerminal(target, session);
        ctx.ui.notify(`Opened VNC terminal for ${validateTarget(target)}, tmux session ${validateSession(session)}.`, "info");
      } catch (error) {
        ctx.ui.notify(error instanceof Error ? error.message : String(error), "error");
      }
    },
  });
}
