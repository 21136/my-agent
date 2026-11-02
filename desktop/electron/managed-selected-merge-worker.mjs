import { parentPort, workerData } from "node:worker_threads";
import { pathToFileURL } from "node:url";
import { waitForMainAuthorization } from "./managed-selected-merge-authorization.mjs";

if (!parentPort || !workerData || workerData.mode !== "managed_selected_merge_worker_v1") {
  throw new Error("managed selected merge worker bootstrap invalid");
}

function authorizeAtMainThread() {
  const signal = new SharedArrayBuffer(Int32Array.BYTES_PER_ELEMENT);
  parentPort.postMessage({ type: "authorization_required", signal });
  return waitForMainAuthorization(signal);
}

try {
  const backendUrl = pathToFileURL(workerData.backendPath).href;
  const backend = await import(backendUrl);
  const receipt = backend.executeManagedSelectedMerge({
    plan: workerData.plan,
    checkpoint: workerData.checkpoint,
    stableRoot: workerData.stableRoot,
    sandboxRoot: workerData.sandboxRoot,
    gitExecutable: workerData.gitExecutable,
    directory: workerData.directory,
    // Every launcher write boundary synchronously asks the trusted Electron main process.
    assertTrusted: authorizeAtMainThread,
  });
  parentPort.postMessage({ type: "finished", receipt });
} catch (error) {
  parentPort.postMessage({
    type: "failed",
    receipt: error && typeof error === "object" ? error.receipt : undefined,
  });
}
