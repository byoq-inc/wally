import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { PiTracer } from "./tracer";
import type { PiTracerRequest, PiTracerRuntimeConfig } from "./types";

async function main() {
  const workspaceRoot = resolve("/root/.openclaw/workspace");
  const fixtureParent = resolve(workspaceRoot, ".artifacts/pi-tracer-fixtures");
  mkdirSync(fixtureParent, { recursive: true });
  const fixtureRepo = mkdtempSync(join(fixtureParent, "fixture-"));
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
    piBinaryPath: process.env.PI_BINARY_PATH || "/usr/local/bin/pi",
    piAuthStrategy: process.env.PI_AUTH_STRATEGY === "bootstrap_required" ? "bootstrap_required" : "reuse_local_pi_auth",
    shellFamily: "bash",
    osFamily: "linux",
  };

  const tracer = new PiTracer(runtime);
  const doctor = await tracer.doctor();

  const outputPath = join(artifactRoot, "doctor.json");
  writeFileSync(outputPath, JSON.stringify(doctor, null, 2));

  if (!doctor.piBinaryFound || doctor.status !== "ok") {
    console.log(JSON.stringify({ phase: "doctor", doctor, outputPath }, null, 2));
    return;
  }

  const targetFile = join(fixtureRepo, "README.md");
  writeFileSync(targetFile, "# Pi Tracer Smoke Fixture\n");
  writeFileSync(join(fixtureRepo, ".gitignore"), "node_modules\n");

  const provider = process.env.PI_SMOKE_PROVIDER || "openai-codex";
  const model = process.env.PI_SMOKE_MODEL || "gpt-5.4";

  const request: PiTracerRequest = {
    workstreamId: "smoke-tracer-run",
    executorRole: "implementer",
    repoPath: fixtureRepo,
    fileScope: ["README.md"],
    goal: `Using provider ${provider} and model ${model}, append a single line exactly equal to TRACER_SMOKE_TEST_LINE to README.md and nothing else.`,
    firstEditTarget: "README.md",
    validationCommands: [["bash", "-lc", "grep -q 'TRACER_SMOKE_TEST_LINE' README.md"]],
    replacementConditions: ["missing proof of work", "no in-scope file changes"],
    deliverBackSchema: "Return the standard final tracer result only.",
    authContext: "reuse_local_pi_auth",
    transport: "rpc_stdio",
    readOnly: false,
    timeouts: {
      proofOfWorkSeconds: 60,
      firstEditSeconds: 180,
      idleSeconds: 180,
    },
  };

  const result = await tracer.run(request);
  console.log(JSON.stringify({ phase: "run", doctor, result }, null, 2));
}

main().catch((error) => {
  console.error(error instanceof Error ? error.stack || error.message : String(error));
  process.exit(1);
});
