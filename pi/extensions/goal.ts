import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";

interface GoalState {
	goal: string;
	updatedAt: string;
}

const SAVE_TYPE = "goal-state";

function normalizeGoal(input: string): string {
	return input.trim().replace(/\s+/g, " ");
}

function truncate(value: string, max: number): string {
	if (value.length <= max) return value;
	return `${value.slice(0, Math.max(0, max - 1))}…`;
}

export default function goalExtension(pi: ExtensionAPI): void {
	let goal = "";

	function persist(): void {
		pi.appendEntry(SAVE_TYPE, {
			goal,
			updatedAt: new Date().toISOString(),
		} satisfies GoalState);
	}

	function reconstruct(ctx: ExtensionContext): void {
		goal = "";
		const entries = ctx.sessionManager.getBranch();
		for (const entry of entries) {
			if (entry.type !== "custom" || entry.customType !== SAVE_TYPE) continue;
			const state = entry.data as Partial<GoalState> | undefined;
			goal = typeof state?.goal === "string" ? state.goal : "";
		}
		updateUi(ctx);
	}

	function updateUi(ctx: ExtensionContext): void {
		if (!goal) {
			ctx.ui.setStatus("goal", undefined);
			ctx.ui.setWidget("goal", undefined);
			return;
		}
		ctx.ui.setStatus("goal", ctx.ui.theme.fg("accent", `goal: ${truncate(goal, 32)}`));
		ctx.ui.setWidget("goal", [ctx.ui.theme.fg("accent", "Goal"), goal]);
	}

	function usage(ctx: ExtensionContext): void {
		ctx.ui.notify(
			[
				"Usage:",
				"/goal <objective>   Set the active objective",
				"/goal append <text>  Add detail to the objective",
				"/goal show           Show current objective",
				"/goal clear          Clear the objective",
			].join("\n"),
			"info",
		);
	}

	pi.on("session_start", async (_event, ctx) => reconstruct(ctx));
	pi.on("session_tree", async (_event, ctx) => reconstruct(ctx));

	pi.registerCommand("goal", {
		description: "Set, show, append to, or clear the active objective",
		handler: async (args, ctx) => {
			const raw = args.trim();
			if (!raw) {
				if (goal) ctx.ui.notify(`Current goal:\n${goal}`, "info");
				else usage(ctx);
				return;
			}

			const [command, ...rest] = raw.split(/\s+/);
			const subcommand = command.toLowerCase();
			const body = rest.join(" ").trim();

			if (["show", "status", "current"].includes(subcommand)) {
				if (goal) ctx.ui.notify(`Current goal:\n${goal}`, "info");
				else ctx.ui.notify("No active goal.", "info");
				return;
			}

			if (["clear", "reset", "off", "done"].includes(subcommand)) {
				goal = "";
				persist();
				updateUi(ctx);
				ctx.ui.notify("Goal cleared.", "info");
				return;
			}

			if (["append", "add"].includes(subcommand)) {
				const addition = normalizeGoal(body);
				if (!addition) {
					ctx.ui.notify("Usage: /goal append <text>", "warning");
					return;
				}
				goal = goal ? `${goal}\n- ${addition}` : addition;
				persist();
				updateUi(ctx);
				ctx.ui.notify("Goal updated.", "info");
				return;
			}

			goal = normalizeGoal(raw);
			persist();
			updateUi(ctx);
			ctx.ui.notify("Goal set.", "info");
		},
	});

	pi.on("before_agent_start", async (event) => {
		if (!goal) return undefined;
		return {
			systemPrompt: `${event.systemPrompt}\n\nActive goal:\n${goal}\n\nKeep this goal in mind across turns. If the user's next request is ambiguous, interpret it in service of this goal. Prefer concise progress updates, call out blockers, and do not drift into unrelated work unless the user changes or clears the goal with /goal clear.`,
		};
	});
}
