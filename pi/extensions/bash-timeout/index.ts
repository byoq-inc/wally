const DEFAULT_BASH_TIMEOUT_SECONDS = 120;
const MAX_BASH_TIMEOUT_SECONDS = 1800;

export function enforceBashTimeout(event) {
  if (event.toolName !== "bash") return undefined;
  const input = event.input;
  if (!input || typeof input !== "object") {
    return { block: true, reason: "Bash input is malformed." };
  }
  if (input.timeout === undefined) {
    input.timeout = DEFAULT_BASH_TIMEOUT_SECONDS;
    return undefined;
  }
  if (typeof input.timeout !== "number" || !Number.isFinite(input.timeout) || input.timeout <= 0) {
    return { block: true, reason: "Bash timeout must be a positive finite number of seconds." };
  }
  if (input.timeout > MAX_BASH_TIMEOUT_SECONDS) {
    return {
      block: true,
      reason: `Bash timeout may not exceed ${MAX_BASH_TIMEOUT_SECONDS} seconds; delegate longer work to the durable async workflow.`,
    };
  }
  return undefined;
}

export default function (pi) {
  pi.on("tool_call", (event) => enforceBashTimeout(event));
  pi.on("before_agent_start", (event) => ({
    systemPrompt:
      event.systemPrompt +
      `\n\nSignal bash contract: omitted timeouts default to ${DEFAULT_BASH_TIMEOUT_SECONDS} seconds. ` +
      `Choose an explicit timeout up to ${MAX_BASH_TIMEOUT_SECONDS} seconds when justified; delegate longer work to the durable async workflow.`,
  }));
}
