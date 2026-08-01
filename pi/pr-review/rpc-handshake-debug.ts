import { PiRpcRunner } from "./pi-rpc-runner";

async function main() {
  const runner = new PiRpcRunner({
    piBinaryPath: "/usr/local/bin/pi",
    cwd: "/root/.openclaw/workspace/.artifacts/pi-tracer-fixtures",
    transport: "rpc_stdio",
    provider: "openai-codex",
    model: "gpt-5.4",
    env: {
      PI_POW_TASK_ID: "rpc-handshake-debug",
    },
    args: ["--no-session"],
  });

  runner.onEvent((event) => {
    console.log("EVENT", JSON.stringify(event.raw));
  });

  console.log("STARTING");
  await runner.start();
  console.log("STARTED");

  const state = await runner.getState();
  console.log("STATE", JSON.stringify(state));

  const promptPromise = runner.prompt("Say only hello, then stop.");
  const timeout = new Promise((_, reject) => setTimeout(() => reject(new Error("prompt timeout after 15s")), 15000));
  const promptResult = await Promise.race([promptPromise, timeout]);
  console.log("PROMPT_RESULT", JSON.stringify(promptResult));

  await runner.stop();
  console.log("STOPPED");
}

main().catch((error) => {
  console.error(error instanceof Error ? error.stack || error.message : String(error));
  process.exit(1);
});
