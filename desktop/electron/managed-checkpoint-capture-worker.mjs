import { parentPort, workerData } from "node:worker_threads";
import { pathToFileURL } from "node:url";
import path from "node:path";
import { waitForMainAuthorization } from "./managed-selected-merge-authorization.mjs";
if (!parentPort || !workerData || workerData.mode !== "managed_source_checkpoint_capture_worker_v1" || typeof workerData.codeRoot !== "string") throw new Error("managed checkpoint capture worker bootstrap invalid");
function authorizeAtMainThread() {
  const signal = new SharedArrayBuffer(Int32Array.BYTES_PER_ELEMENT);
  parentPort.postMessage({ type: "authorization_required", signal });
  return waitForMainAuthorization(signal);
}
try {
  if (!authorizeAtMainThread()) throw new Error("untrusted checkpoint caller");
  const backendUrl = pathToFileURL(path.join(workerData.codeRoot, "desktop", "launcher", "managed-source-checkpoint.mjs")).href;
  const backend = await import(backendUrl);
  const pack = backend.captureManagedSourceCheckpoint({ ...workerData.captureInput, ...workerData.roots });
  parentPort.postMessage({ type: "finished", pack });
} catch {
  parentPort.postMessage({ type: "failed" });
}
