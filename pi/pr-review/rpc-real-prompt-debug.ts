import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { PiRpcRunner } from "./pi-rpc-runner";

async function main() {
  const workspaceRoot = resolve("/root/.openclaw/workspace");
  const fixtureParent = resolve(workspaceRoot, ".artifacts/pi-tracer-fixtures");
  mkdirSync(fixtureParent, { recursive: true });
  const fixtureRepo = mkdtempSync(join(fixtureParent, "rpc-real-prompt-"));
  writeFileSync(join(fixtureRepo, ".gitignore"), "node_modules\n");
  writeFileSync(join(fixtureRepo, "README.md"), "# Pi Tracer Smoke Fixture\n");

  const prompt = [
    `You are executing one bounded coding task.`,
    `The pi-proof-of-work skill is installed; finish by calling proof_of_work exactly once.`,
    `Use PI_POW_TASK_ID as the task id for proof_of_work.`,
    `Do not modify files outside the declared file scope.`,
    `Do not touch .git, caches, build outputs, or unrelated files.`,
    `Goal: Using provider openai-codex and model gpt-5.4, append a single line exactly equal to TRACER_SMOKE_TEST_LINE to README.md and nothing else.`,
    `File scope: README.md`,
    `First edit target: README.md`,
    `Validation commands: bash -lc grep -q 'TRACER_SMOKE_TEST_LINE' README.md`,
    `Generic phrases like 'relevant files' are invalid. Use exact paths.`,
  ].join("\n\n");

  const runner = new PiRpcRunner({
    piBinaryPath: "/usr/local/bin/pi",
    cwd: fixtureRepo,
    transport: "rpc_stdio",
    provider: "openai-codex",
    model: "gpt-5.4",
    env: {
      PI_POW_TASK_ID: "rpc-real-prompt-debug",
    },
    args: ["--no-session"],
  });

  runner.onEvent((event) => {
    console.log("EVENT", JSON.stringify(event.raw));
  });

  console.log("CWD", fixtureRepo);
  console.log("PROMPT_LEN", prompt.length);
  await runner.start();
  console.log("STARTED");

  const promptPromise = runner.prompt(prompt);
  const timeout = new Promise((_, reject) => setTimeout(() => reject(new Error("prompt timeout after 20s")), 20000));
  const promptResult = await Promise.race([promptPromise, timeout]);
  console.log("PROMPT_RESULT", JSON.stringify(promptResult));

  await new Promise((resolve) => setTimeout(resolve, 5000));
  await runner.stop();
  console.log("STOPPED");
}

main().catch((error) => {
  console.error(error instanceof Error ? error.stack || error.message : String(error));
  process.exit(1);
});
