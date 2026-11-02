import path from "node:path";
import { Worker } from "node:worker_threads";
export function runManagedSourceAnalysis({ codeRoot, operation, roots, checkpoint, isTrusted }) {
 return new Promise((resolve, reject) => {
  const worker = new Worker(path.join(codeRoot, "desktop", "electron", "managed-source-analysis-worker.mjs"), {
   workerData: { mode: "managed_source_analysis_worker_v1", codeRoot, operation, roots, checkpoint },
   env: { PATH: process.env.PATH ?? process.env.Path ?? "", SYSTEMROOT: process.env.SYSTEMROOT ?? process.env.SystemRoot ?? "", WINDIR: process.env.WINDIR ?? process.env.Windir ?? "" },
  });
  let settled = false;
  const finish = (error, value) => { if (settled) return; settled = true; if (error) { reject(error); void worker.terminate(); } else resolve(value); };
  worker.on("message", (message) => {
   if (!message || typeof message !== "object") { finish(new Error("managed source analysis worker protocol invalid")); return; }
   if (message.type === "authorization_required") {
    const signal = message.signal instanceof SharedArrayBuffer && message.signal.byteLength === Int32Array.BYTES_PER_ELEMENT ? new Int32Array(message.signal) : null;
    let allowed = false; try { allowed = Boolean(signal && isTrusted()); } catch { allowed = false; }
    if (!signal) { finish(new Error("managed source analysis authorization channel invalid")); return; }
    Atomics.store(signal, 0, allowed ? 1 : 2); Atomics.notify(signal, 0); return;
   }
   if (message.type === "finished") { finish(null, message); return; }
   if (message.type === "failed") {
    const error = new Error("managed source analysis worker failed");
    if (typeof message.code === "string" && /^[a-z][a-z0-9_]{0,63}$/.test(message.code) && Array.isArray(message.paths) && message.paths.every(value => typeof value === "string" && value.length <= 512)) {
     error.code = message.code; error.paths = message.paths;
    }
    finish(error); return;
   }
   finish(new Error("managed source analysis worker protocol invalid"));
  });
  worker.once("error", error => finish(error));
  worker.once("exit", code => { if (!settled) finish(new Error(`managed source analysis worker exited (${code})`)); });
 });
}
