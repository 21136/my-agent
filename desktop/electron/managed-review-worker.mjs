import path from "node:path";
import { parentPort, workerData } from "node:worker_threads";
import { pathToFileURL } from "node:url";
import { waitForMainAuthorization } from "./managed-selected-merge-authorization.mjs";
import { resolveManagedReviewRoots } from "./managed-review-roots.mjs";

if (!parentPort || !workerData || workerData.mode !== "managed_source_review_worker_v1" || typeof workerData.codeRoot !== "string") {
  throw new Error("managed source review worker bootstrap invalid");
}

function authorizeAtMainThread() {
  const signal = new SharedArrayBuffer(Int32Array.BYTES_PER_ELEMENT);
  parentPort.postMessage({ type: "authorization_required", signal });
  return waitForMainAuthorization(signal);
}

let phase = "launcher_load";
try {
  const launcher = path.join(workerData.codeRoot, "desktop", "launcher");
  const plan = await import(pathToFileURL(path.join(launcher, "managed-source-plan.mjs")).href);
  phase = "identity_inspection";
  const roots = await resolveManagedReviewRoots(workerData.codeRoot, async (candidateRoots) => {
    phase = "identity_authorization";
    if (!authorizeAtMainThread()) throw new Error("untrusted managed review caller");
    phase = "identity_git_scan";
    return plan.inspectManagedSources({ ...candidateRoots, metadataOnlyHistory: true });
  });
  if (!authorizeAtMainThread()) throw new Error("untrusted managed review caller");
  phase = "review_authorization";
  if (!authorizeAtMainThread()) throw new Error("untrusted managed review caller");
  phase = "review_inspection";
  const { inspectManagedReview } = await import(pathToFileURL(path.join(launcher, "managed-review.mjs")).href);
  const report = await inspectManagedReview(roots);
  parentPort.postMessage({ type: "finished", roots, report });
} catch {
  parentPort.postMessage({ type: "failed", phase });
}
