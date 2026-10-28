import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { StringEnum } from "@mariozechner/pi-ai";
import { Type } from "typebox";
import { execFile, spawn } from "node:child_process";
import { promisify } from "node:util";
import { mkdtemp, readFile, writeFile, readdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { lookup } from "node:dns/promises";
import net from "node:net";

const execFileAsync = promisify(execFile);
const DISPLAY = process.env.PI_VNC_DISPLAY || ":1";
const CHROME_PORT = Number(process.env.PI_CHROME_DEBUG_PORT || 9222);
const CHROME_BIN = process.env.PI_CHROME_BIN || "/usr/bin/google-chrome";
const BRAVE_SCRIPT = process.env.PI_BRAVE_SEARCH_SCRIPT || "/root/.pi/agent/skills/web-search/scripts/brave_search.py";
const SECRET_ENV_KEYS = ["OP_SERVICE_ACCOUNT", "OP_SERVICE_ACCOUNT_TOKEN", "BRAVE_API_KEY", "BRAVE_API_KEY_OP_REF"] as const;
let cachedSecretEnv: NodeJS.ProcessEnv | undefined;

function textResult(text: string, details: any = {}) {
  return { content: [{ type: "text" as const, text }], details };
}

async function tmuxSockets(): Promise<string[]> {
  const dirs = Array.from(new Set([
    process.env.OPENCLAW_TMUX_SOCKET_DIR,
    process.env.CLAWDBOT_TMUX_SOCKET_DIR,
    join(tmpdir(), "openclaw-tmux-sockets"),
  ].filter(Boolean) as string[]));
  const sockets: string[] = [];
  for (const dir of dirs) {
    try {
      for (const entry of await readdir(dir)) {
        if (entry.endsWith(".sock")) sockets.push(join(dir, entry));
      }
    } catch {}
  }
  return sockets;
}

async function readTmuxEnv(name: string): Promise<string | undefined> {
  for (const socket of await tmuxSockets()) {
    try {
      const { stdout } = await execFileAsync("tmux", ["-S", socket, "show-environment", "-g", name], {
        timeout: 2_000,
        maxBuffer: 64 * 1024,
      });
      const line = stdout.trim();
      if (line.startsWith(`${name}=`)) return line.slice(name.length + 1);
    } catch {}
  }
}

async function toolSecretEnv(): Promise<NodeJS.ProcessEnv> {
  if (cachedSecretEnv) return cachedSecretEnv;
  const env: NodeJS.ProcessEnv = { ...process.env };
  for (const key of SECRET_ENV_KEYS) {
    if (!env[key]) {
      const value = await readTmuxEnv(key);
      if (value) env[key] = value;
    }
  }
  cachedSecretEnv = env;
  return env;
}

function isPrivateIp(ip: string): boolean {
  if (net.isIPv4(ip)) {
    const p = ip.split(".").map(Number);
    return p[0] === 10 || p[0] === 127 || (p[0] === 169 && p[1] === 254) || (p[0] === 172 && p[1] >= 16 && p[1] <= 31) || (p[0] === 192 && p[1] === 168) || p[0] === 0;
  }
  if (net.isIPv6(ip)) return ip === "::1" || ip.startsWith("fc") || ip.startsWith("fd") || ip.startsWith("fe80");
  return true;
}

async function assertPublicHttpUrl(raw: string) {
  const url = new URL(raw);
  if (!["http:", "https:", "data:"].includes(url.protocol)) throw new Error("Only http/https/data URLs are allowed.");
  if (url.protocol === "data:") return url;
  const addresses = await lookup(url.hostname, { all: true });
  if (!addresses.length || addresses.some((a) => isPrivateIp(a.address))) throw new Error(`Blocked private/internal host: ${url.hostname}`);
  return url;
}

function htmlToText(input: string, mode: "markdown" | "text") {
  let s = input
    .replace(/<script[\s\S]*?<\/script>/gi, "")
    .replace(/<style[\s\S]*?<\/style>/gi, "")
    .replace(/<noscript[\s\S]*?<\/noscript>/gi, "");
  if (mode === "markdown") {
    s = s
      .replace(/<h1[^>]*>([\s\S]*?)<\/h1>/gi, "\n# $1\n")
      .replace(/<h2[^>]*>([\s\S]*?)<\/h2>/gi, "\n## $1\n")
      .replace(/<h3[^>]*>([\s\S]*?)<\/h3>/gi, "\n### $1\n")
      .replace(/<a\s+[^>]*href=["']([^"']+)["'][^>]*>([\s\S]*?)<\/a>/gi, "$2 ($1)")
      .replace(/<li[^>]*>/gi, "\n- ")
      .replace(/<\/p>|<br\s*\/?>|<\/div>/gi, "\n");
  }
  return s
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/[ \t]+/g, " ")
    .replace(/\n\s+/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

async function guardedFetch(rawUrl: string, mode: "markdown" | "text", maxChars: number, redirects = 3): Promise<{ url: string; status: number; contentType: string; text: string; truncated: boolean }> {
  let url = await assertPublicHttpUrl(rawUrl);
  if (url.protocol === "data:") return { url: rawUrl, status: 200, contentType: "text/plain", text: rawUrl.slice(0, maxChars), truncated: rawUrl.length > maxChars };
  for (let i = 0; i <= redirects; i++) {
    const res = await fetch(url, { redirect: "manual", headers: { "User-Agent": "Mozilla/5.0 pi-web-tools/1.0", "Accept-Language": "en-US,en;q=0.9" } });
    if ([301, 302, 303, 307, 308].includes(res.status)) {
      const loc = res.headers.get("location");
      if (!loc) throw new Error(`Redirect ${res.status} without Location`);
      url = await assertPublicHttpUrl(new URL(loc, url).toString());
      continue;
    }
    const contentType = res.headers.get("content-type") || "";
    const raw = await res.text();
    let text = contentType.includes("html") ? htmlToText(raw, mode) : raw;
    const truncated = text.length > maxChars;
    if (truncated) text = text.slice(0, maxChars) + "\n\n[truncated]";
    return { url: url.toString(), status: res.status, contentType, text, truncated };
  }
  throw new Error("Too many redirects");
}

async function chromeVersion() {
  const res = await fetch(`http://127.0.0.1:${CHROME_PORT}/json/version`);
  if (!res.ok) throw new Error(`Chrome DevTools not reachable on ${CHROME_PORT}`);
  return res.json() as Promise<{ webSocketDebuggerUrl: string }>;
}

async function getPageWs() {
  const res = await fetch(`http://127.0.0.1:${CHROME_PORT}/json/list`);
  if (!res.ok) throw new Error("Chrome page list unavailable");
  const pages = (await res.json()) as Array<{ id: string; type: string; webSocketDebuggerUrl: string }>;
  let page = pages.find((p) => p.type === "page") || pages[0];
  if (!page) {
    const created = await fetch(`http://127.0.0.1:${CHROME_PORT}/json/new?about:blank`, { method: "PUT" });
    if (!created.ok) throw new Error("No Chrome page found and creating one failed");
    page = await created.json() as any;
  }
  return page.webSocketDebuggerUrl;
}

async function cdp(method: string, params: any = {}, wsUrl?: string): Promise<any> {
  const url = wsUrl || await getPageWs();
  const WebSocketCtor = (globalThis as any).WebSocket;
  if (!WebSocketCtor) throw new Error("Node WebSocket global unavailable");
  return new Promise((resolve, reject) => {
    const ws = new WebSocketCtor(url);
    const id = 1;
    const timer = setTimeout(() => { try { ws.close(); } catch {} reject(new Error(`CDP timeout: ${method}`)); }, 20_000);
    ws.onopen = () => ws.send(JSON.stringify({ id, method, params }));
    ws.onerror = (e: any) => { clearTimeout(timer); reject(e?.error || new Error("CDP websocket error")); };
    ws.onmessage = async (ev: any) => {
      let data = ev.data;
      if (data && typeof data.arrayBuffer === "function") data = Buffer.from(await data.arrayBuffer()).toString("utf8");
      else if (Buffer.isBuffer(data)) data = data.toString("utf8");
      else if (data instanceof ArrayBuffer) data = Buffer.from(data).toString("utf8");
      else data = String(data);
      const msg = JSON.parse(data);
      if (msg.id !== id) return;
      clearTimeout(timer);
      ws.close();
      if (msg.error) reject(new Error(`${method}: ${msg.error.message}`));
      else resolve(msg.result);
    };
  });
}

async function ensureChrome() {
  try { await chromeVersion(); return { started: false, port: CHROME_PORT }; } catch {}
  const userDir = process.env.PI_CHROME_USER_DATA_DIR || join(process.env.HOME || "/root", ".pi", "agent", "chrome-profile");
  spawn(CHROME_BIN, [
    `--remote-debugging-port=${CHROME_PORT}`,
    `--user-data-dir=${userDir}`,
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--new-window",
    "about:blank",
  ], { env: { ...process.env, DISPLAY }, detached: true, stdio: "ignore" }).unref();
  for (let i = 0; i < 20; i++) {
    await new Promise((r) => setTimeout(r, 500));
    try { await chromeVersion(); return { started: true, port: CHROME_PORT, userDir }; } catch {}
  }
  throw new Error("Chrome did not start with DevTools");
}

async function vncShot(): Promise<string> {
  const dir = await mkdtemp(join(tmpdir(), "pi-vnc-"));
  const file = join(dir, "screenshot.png");
  await execFileAsync("scrot", [file], { env: { ...process.env, DISPLAY }, timeout: 10_000 });
  return file;
}

export default function (pi: ExtensionAPI) {
  pi.registerTool({
    name: "web_search",
    label: "Web Search",
    description: "Search the live web with Brave Search. Uses the existing 1Password-backed web-search skill script.",
    promptSnippet: "Search live web results with Brave.",
    parameters: Type.Object({
      query: Type.String(),
      count: Type.Optional(Type.Number()),
      freshness: Type.Optional(StringEnum(["day", "week", "month", "year", "pd", "pw", "pm", "py"] as const)),
      country: Type.Optional(Type.String()),
      language: Type.Optional(Type.String()),
    }),
    async execute(_id, params) {
      if (!existsSync(BRAVE_SCRIPT)) throw new Error(`Missing Brave script: ${BRAVE_SCRIPT}`);
      const args = [BRAVE_SCRIPT, params.query, "--count", String(params.count || 5)];
      if (params.freshness) args.push("--freshness", params.freshness);
      if (params.country) args.push("--country", params.country);
      if (params.language) args.push("--language", params.language);
      const { stdout } = await execFileAsync("python3", args, { timeout: 30_000, maxBuffer: 1024 * 1024, env: await toolSecretEnv() });
      return textResult(stdout.trim(), JSON.parse(stdout));
    },
  });

  pi.registerTool({
    name: "web_fetch",
    label: "Web Fetch",
    description: "Fetch a public http/https URL and extract readable text/markdown. Blocks private/internal hosts and limits redirects/output.",
    promptSnippet: "Fetch public URLs and extract readable text/markdown.",
    parameters: Type.Object({
      url: Type.String(),
      extractMode: Type.Optional(StringEnum(["markdown", "text"] as const)),
      maxChars: Type.Optional(Type.Number()),
    }),
    async execute(_id, params) {
      const result = await guardedFetch(params.url, params.extractMode || "markdown", Math.min(params.maxChars || 20_000, 50_000));
      return textResult(result.text, result);
    },
  });

  pi.registerTool({
    name: "chrome_start",
    label: "Chrome Start",
    description: "Start or verify Google Chrome on the VPS VNC display with DevTools enabled.",
    parameters: Type.Object({}),
    async execute() {
      const result = await ensureChrome();
      return textResult(`Chrome ready on DISPLAY=${DISPLAY}, DevTools port ${CHROME_PORT}${result.started ? " (started)" : " (already running)"}`, result);
    },
  });

  pi.registerTool({
    name: "chrome_navigate",
    label: "Chrome Navigate",
    description: "Navigate headless/VNC Chrome to a URL using Chrome DevTools Protocol.",
    parameters: Type.Object({ url: Type.String() }),
    async execute(_id, params) {
      await ensureChrome();
      const url = (await assertPublicHttpUrl(params.url)).toString();
      await cdp("Page.navigate", { url });
      await new Promise((r) => setTimeout(r, 1500));
      const title = await cdp("Runtime.evaluate", { expression: "document.title", returnByValue: true });
      return textResult(`Navigated to ${url}\nTitle: ${title.result?.value || ""}`, { url, title: title.result?.value });
    },
  });

  pi.registerTool({
    name: "chrome_eval",
    label: "Chrome Eval",
    description: "Evaluate JavaScript in the active Chrome page. Use for page inspection, not secrets.",
    parameters: Type.Object({ expression: Type.String() }),
    async execute(_id, params) {
      await ensureChrome();
      const result = await cdp("Runtime.evaluate", { expression: params.expression, returnByValue: true, awaitPromise: true });
      return textResult(JSON.stringify(result.result?.value ?? result.result ?? null, null, 2), result);
    },
  });

  pi.registerTool({
    name: "chrome_screenshot",
    label: "Chrome Screenshot",
    description: "Capture active Chrome page screenshot via DevTools and save it to a temp PNG.",
    parameters: Type.Object({}),
    async execute() {
      await ensureChrome();
      await cdp("Page.enable");
      const shot = await cdp("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
      const dir = await mkdtemp(join(tmpdir(), "pi-chrome-"));
      const file = join(dir, "screenshot.png");
      await writeFile(file, Buffer.from(shot.data, "base64"));
      return textResult(`Chrome screenshot saved: ${file}`, { file });
    },
  });

  pi.registerTool({
    name: "vnc_screenshot",
    label: "VNC Screenshot",
    description: "Capture the VPS VNC display screenshot to a temp PNG.",
    parameters: Type.Object({}),
    async execute() {
      const file = await vncShot();
      return textResult(`VNC screenshot saved: ${file}`, { file, display: DISPLAY });
    },
  });

  pi.registerTool({
    name: "vnc_click",
    label: "VNC Click",
    description: "Click coordinates on the VPS VNC display using xdotool.",
    parameters: Type.Object({ x: Type.Number(), y: Type.Number(), button: Type.Optional(Type.Number()) }),
    async execute(_id, params) {
      await execFileAsync("xdotool", ["mousemove", String(params.x), String(params.y), "click", String(params.button || 1)], { env: { ...process.env, DISPLAY }, timeout: 10_000 });
      return textResult(`Clicked ${params.x},${params.y} on DISPLAY=${DISPLAY}`);
    },
  });

  pi.registerTool({
    name: "vnc_type",
    label: "VNC Type",
    description: "Type text into the VPS VNC display using xdotool.",
    parameters: Type.Object({ text: Type.String() }),
    async execute(_id, params) {
      await execFileAsync("xdotool", ["type", "--delay", "1", params.text], { env: { ...process.env, DISPLAY }, timeout: 20_000 });
      return textResult(`Typed ${params.text.length} characters on DISPLAY=${DISPLAY}`);
    },
  });

  pi.registerTool({
    name: "vnc_key",
    label: "VNC Key",
    description: "Send a key chord to the VPS VNC display using xdotool, e.g. Return, ctrl+l, alt+Tab.",
    parameters: Type.Object({ key: Type.String() }),
    async execute(_id, params) {
      await execFileAsync("xdotool", ["key", params.key], { env: { ...process.env, DISPLAY }, timeout: 10_000 });
      return textResult(`Sent key ${params.key} on DISPLAY=${DISPLAY}`);
    },
  });
}
