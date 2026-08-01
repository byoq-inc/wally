import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { PiTracer } from "./tracer";
import type { PiTracerRequest, PiTracerRuntimeConfig } from "./types";

async function main() {
  const workspaceRoot = resolve("/root/.openclaw/workspace");
  const fixtureParent = resolve(workspaceRoot, ".artifacts/pi-tracer-fixtures");
  mkdirSync(fixtureParent, { recursive: true });
  const fixtureRepo = mkdtempSync(join(fixtureParent, "blocked-fixture-"));
  const artifactRoot = resolve(workspaceRoot, ".artifacts/pi-tracer-smoke");
  const logRoot = resolve(workspaceRoot, ".logs/pi-tracer-smoke");

  mkdirSync(artifactRoot, { recursive: true });
  mkdirSync(logRoot, { recursive: true });

  const runtime: PiTracerRuntimeConfig = {
    runtimeHostId: "local-smoke",
    runtimeHostKind: "vps",
    workspaceRoot,
    artifactRoot,
    logRoot,
    piBinaryPath: "/usr/local/bin/pi",
    piAuthStrategy: "reuse_local_pi_auth",
    shellFamily: "bash",
    osFamily: "linux",
  };

  const tracer = new PiTracer(runtime);
  const readmePath = join(fixtureRepo, "README.md");
  writeFileSync(readmePath, "# Blocked Fast Test Fixture\n");

  const request: PiTracerRequest = {
    workstreamId: "blocked-fast-test",
    executorRole: "implementer",
    repoPath: fixtureRepo,
    fileScope: ["README.md"],
    goal: "Using provider openai-codex and model gpt-5.4, append a single line exactly equal to TRACER_SMOKE_TEST_LINE to README.md and nothing else.",
    firstEditTarget: "README.md",
    validationCommands: [["bash", "-lc", "grep -q 'TRACER_SMOKE_TEST_LINE' README.md"]],
    replacementConditions: ["missing proof of work", "no in-scope file changes"],
    deliverBackSchema: "Return the standard final tracer result only.",
    authContext: "reuse_local_pi_auth",
    transport: "rpc_stdio",
    readOnly: false,
    timeouts: {
      proofOfWorkSeconds: 30,
      firstEditSeconds: 30,
      idleSeconds: 30,
    },
  };

  const result = await tracer.run(request);
  console.log(JSON.stringify(result, null, 2));
}

main().catch((error) => {
  console.error(error instanceof Error ? error.stack || error.message : String(error));
  process.exit(1);
});
