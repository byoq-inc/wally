import assert from "node:assert/strict";
import { createJiti } from "/usr/lib/node_modules/@earendil-works/pi-coding-agent/node_modules/jiti/lib/jiti.mjs";

const jiti = createJiti(import.meta.url);
const { enforceBashTimeout } = await jiti.import("./index.ts");

const omitted = { toolName: "bash", input: { command: "sleep 999" } };
assert.equal(enforceBashTimeout(omitted), undefined);
assert.equal(omitted.input.timeout, 120);

const explicit = { toolName: "bash", input: { command: "sleep 1", timeout: 600 } };
assert.equal(enforceBashTimeout(explicit), undefined);
assert.equal(explicit.input.timeout, 600);

for (const timeout of [0, -1, Number.NaN, Number.POSITIVE_INFINITY, "120"]) {
  const event = { toolName: "bash", input: { command: "true", timeout } };
  assert.equal(enforceBashTimeout(event)?.block, true);
}

const excessive = { toolName: "bash", input: { command: "true", timeout: 1801 } };
assert.equal(enforceBashTimeout(excessive)?.block, true);

const maximum = { toolName: "bash", input: { command: "true", timeout: 1800 } };
assert.equal(enforceBashTimeout(maximum), undefined);

const read = { toolName: "read", input: { path: "/tmp/example" } };
assert.equal(enforceBashTimeout(read), undefined);
assert.deepEqual(read.input, { path: "/tmp/example" });

console.log("PASS: bash timeout policy");
