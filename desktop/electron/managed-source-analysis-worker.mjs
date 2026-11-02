import { parentPort, workerData } from "node:worker_threads";
import { pathToFileURL } from "node:url";
import path from "node:path";
import { waitForMainAuthorization } from "./managed-selected-merge-authorization.mjs";
if (!parentPort || !workerData || workerData.mode !== "managed_source_analysis_worker_v1" || typeof workerData.codeRoot !== "string") throw new Error("managed source analysis worker bootstrap invalid");
function authorizeAtMainThread() { const signal = new SharedArrayBuffer(Int32Array.BYTES_PER_ELEMENT); parentPort.postMessage({ type: "authorization_required", signal }); return waitForMainAuthorization(signal); }
function requireTrust() { if (!authorizeAtMainThread()) throw new Error("untrusted managed source caller"); }
async function main() {
 const launcher = path.join(workerData.codeRoot, "desktop", "launcher");
 if (workerData.operation === "identity") { requireTrust(); const plan = await import(pathToFileURL(path.join(launcher, "managed-source-plan.mjs")).href); const identity = await plan.inspectManagedSources({ ...workerData.roots, metadataOnlyHistory: true }); parentPort.postMessage({ type: "finished", identity }); return; }
 if (workerData.operation === "review") { requireTrust(); const { inspectManagedReview } = await import(pathToFileURL(path.join(launcher, "managed-review.mjs")).href); const report = await inspectManagedReview(workerData.roots); parentPort.postMessage({ type: "finished", report }); return; }
 if (workerData.operation === "prepare") { requireTrust(); const backend = await import(pathToFileURL(path.join(launcher, "managed-selected-merge.mjs")).href); const plan = await backend.prepareManagedSelectedMerge({ stableRoot: workerData.roots.stableRoot, sandboxRoot: workerData.roots.sandboxRoot, checkpoint: workerData.checkpoint, gitExecutable: "C:\\Program Files\\Git\\cmd\\git.exe", preserveStableWorkingChanges: true }); parentPort.postMessage({ type: "finished", plan }); return; }
 throw new Error("managed source analysis operation invalid");
}
try { await main(); } catch (error) {
 const code = workerData.operation === "prepare" && /^[a-z][a-z0-9_]{0,63}$/.test(error?.code ?? "") && Array.isArray(error?.paths) ? error.code : undefined;
 const paths = code ? error.paths.filter(value => typeof value === "string" && value.length <= 512) : undefined;
 parentPort.postMessage({ type: "failed", ...(code ? { code, paths } : {}) });
}
