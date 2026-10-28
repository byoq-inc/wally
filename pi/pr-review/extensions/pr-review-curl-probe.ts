import { execFile } from "node:child_process";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

export interface CurlProbeResult {
  url: string;
  ok: boolean;
  statusCode?: number;
  stdout?: string;
  stderr?: string;
  error?: string;
}

export async function runCurlProbe(url: string, timeoutSeconds = 15): Promise<CurlProbeResult> {
  try {
    const { stdout, stderr } = await execFileAsync(
      "curl",
      [
        "-sS",
        "-L",
        "-o",
        "/dev/null",
        "-w",
        "%{http_code}",
        "--max-time",
        String(timeoutSeconds),
        url,
      ],
      {},
    );
    const statusCode = Number.parseInt(stdout.trim(), 10);
    return {
      url,
      ok: Number.isFinite(statusCode) && statusCode >= 200 && statusCode < 400,
      statusCode,
      stdout,
      stderr,
    };
  } catch (error) {
    const err = error as Error & { stdout?: string; stderr?: string };
    return {
      url,
      ok: false,
      stdout: err.stdout,
      stderr: err.stderr,
      error: err.message,
    };
  }
}
