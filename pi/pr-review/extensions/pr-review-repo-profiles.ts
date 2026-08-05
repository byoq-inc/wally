import type { RepoStartupProfile } from "./pr-review-types";

/**
 * Sanitized deployment shape.
 *
 * The live host contains repository-specific product paths and service names.
 * They are private application configuration rather than harness source, so the
 * export preserves one inert example profile. Deployments should generate this
 * mapping from host-only configuration.
 */
export const REPO_STARTUP_PROFILES: Record<string, RepoStartupProfile[]> = {
  "example-owner/example-repository": [
    {
      name: "example-frontend",
      cwd: "frontend",
      command: ["npm", "run", "dev", "--", "--hostname", "127.0.0.1", "--port", "3100"],
      port: 3100,
      healthUrls: ["http://127.0.0.1:3100"],
      matchPaths: ["frontend/", "shared/"],
      installCommand: ["npm", "install"],
    },
    {
      name: "example-api",
      cwd: "api",
      command: ["uv", "run", "uvicorn", "api.main:app", "--host", "127.0.0.1", "--port", "3200"],
      port: 3200,
      healthUrls: ["http://127.0.0.1:3200/health"],
      matchPaths: ["api/"],
      env: {
        DATABASE_URL: "<provided-by-host-environment>",
      },
      checkCommands: [["uv", "run", "pytest", "-q"]],
    },
  ],
};
