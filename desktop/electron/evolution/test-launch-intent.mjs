import { lstatSync } from "node:fs";
import path from "node:path";

function presentOrUncertain(target) {
  try { lstatSync(target); return true; }
  catch (error) { return error?.code !== "ENOENT"; }
}

/** Detect test-host intent without creating or mutating any path. */
export function detectEvolutionTestLaunchIntent(appRoot, argv = process.argv, env = process.env) {
  if (typeof appRoot !== "string" || !path.isAbsolute(appRoot)) return true;
  if (Array.isArray(argv) && argv.some(value => (value === "--evolution-test-profile" || value.startsWith("--evolution-test-profile=")) || value === "--ordinary-chat-h1")) return true;
  for (const key of ["MY_AGENT_H1_RUN_ID", "MY_AGENT_H1_PROFILE_ROOT", "MY_AGENT_ELECTRON_USER_DATA", "MY_AGENT_ELECTRON_SESSION_DATA"])
    if (typeof env?.[key] === "string" && env[key].length > 0) return true;
  const resolvedRoot = path.resolve(appRoot);
  const hostRoot = path.dirname(resolvedRoot);
  const runRoot = path.dirname(hostRoot);
  if (path.basename(resolvedRoot).toLowerCase() !== "app" || path.basename(hostRoot).toLowerCase() !== "host") return false;
  const runParent = path.dirname(runRoot);
  let ancestor = runParent;
  while (ancestor !== path.dirname(ancestor) && path.basename(ancestor).toLowerCase() !== "artifacts") ancestor = path.dirname(ancestor);
  if (ancestor === path.dirname(ancestor)) return false;

  // UUID layouts produced by the launcher must not fall back when every
  // identity file was removed. The loader also permits marked runs at any
  // depth inside artifacts, but unrelated unmarked nested paths remain ordinary.
  const canonicalRunParent = runParent === ancestor ||
    (path.basename(runParent).toLowerCase() === "ordinary-chat-h1" && path.dirname(runParent) === ancestor);
  if (canonicalRunParent && /^run-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(path.basename(runRoot))) return true;
  return [
    path.join(runRoot, ".evolution-test-run"),
    path.join(runRoot, ".ordinary-chat-h1.json"),
    path.join(runRoot, ".workbench-crash-reopen-recovery.json"),
    path.join(hostRoot, "evolution-test-profile.json"),
  ].some(presentOrUncertain);
}

export function assertEvolutionTestProfileLoaded(testIntent, profile, loadError) {
  if (testIntent && !profile) {
    const error = new Error("evolution test startup intent detected but its profile failed to load; refusing ordinary Host fallback");
    error.code = "permission_denied";
    if (loadError) error.cause = loadError;
    throw error;
  }
}
