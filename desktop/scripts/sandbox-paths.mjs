import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const desktopDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = path.resolve(desktopDir, "..");

export const DEFAULT_SANDBOX_REL = path.join(".worktrees", "sandbox");

export function resolveSandboxRoot() {
  const raw = (process.env.MY_AGENT_SANDBOX_ROOT || "").trim();
  if (raw) return path.resolve(raw);
  return path.join(repoRoot, DEFAULT_SANDBOX_REL);
}

/** @returns {{ sandboxRoot: string, sandboxDesktop: string, active: boolean, previewCwd: string, previewViteBin: string, previewConfig: string, label: string }} */
export function resolvePreviewLanePaths() {
  const sandboxRoot = resolveSandboxRoot();
  const sandboxDesktop = path.join(sandboxRoot, "desktop");
  const stableViteBin = path.join(desktopDir, "node_modules", "vite", "bin", "vite.js");
  const stablePreviewConfig = path.join(desktopDir, "vite.shell-preview.config.ts");

  const sandboxReady =
    existsSync(path.join(sandboxDesktop, "package.json"))
    && existsSync(path.join(sandboxRoot, "agent-core"));

  if (!sandboxReady) {
    return {
      sandboxRoot,
      sandboxDesktop,
      active: false,
      previewCwd: desktopDir,
      previewViteBin: stableViteBin,
      previewConfig: stablePreviewConfig,
      label: "stable (no sandbox — run init-sandbox.bat for isolated experiments)",
    };
  }

  const sandboxVite = path.join(sandboxDesktop, "node_modules", "vite", "bin", "vite.js");
  const sandboxConfig = path.join(sandboxDesktop, "vite.shell-preview.config.ts");
  const previewViteBin = existsSync(sandboxVite) ? sandboxVite : stableViteBin;
  const previewConfig = existsSync(sandboxConfig) ? sandboxConfig : stablePreviewConfig;

  return {
    sandboxRoot,
    sandboxDesktop,
    active: true,
    previewCwd: sandboxDesktop,
    previewViteBin,
    previewConfig,
    label: `sandbox (${path.relative(repoRoot, sandboxRoot) || sandboxRoot})`,
  };
}
