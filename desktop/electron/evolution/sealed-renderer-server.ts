import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { lstatSync, readFileSync, realpathSync } from "node:fs";
import * as path from "node:path";

const LOOPBACK = "127.0.0.1";
const CONTENT_TYPES: Record<string, string> = {
  ".css": "text/css; charset=utf-8",
  ".gif": "image/gif",
  ".html": "text/html; charset=utf-8",
  ".ico": "image/x-icon",
  ".jpeg": "image/jpeg",
  ".jpg": "image/jpeg",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".txt": "text/plain; charset=utf-8",
  ".wasm": "application/wasm",
  ".webp": "image/webp",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
};

export type EvolutionTestRendererServer = {
  origin: string;
  indexUrl: string;
  port: number;
  close(): Promise<void>;
};

function inside(root: string, target: string): boolean {
  const relative = path.relative(root, target);
  return relative !== "" && relative !== ".." && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative);
}

function samePath(left: string, right: string): boolean {
  const a = path.resolve(left);
  const b = path.resolve(right);
  return process.platform === "win32" ? a.toLowerCase() === b.toLowerCase() : a === b;
}

function trustedDirectory(value: string): string {
  if (!path.isAbsolute(value)) throw new Error("sealed renderer root must be absolute");
  const absolute = path.resolve(value);
  const info = lstatSync(absolute);
  if (!info.isDirectory() || info.isSymbolicLink()) throw new Error("sealed renderer root must be a real directory");
  const real = realpathSync.native(absolute);
  if (!samePath(real, absolute)) throw new Error("sealed renderer root resolved through a link");
  const index = path.join(real, "index.html");
  const indexInfo = lstatSync(index);
  if (!indexInfo.isFile() || indexInfo.isSymbolicLink() || !samePath(realpathSync.native(index), index)) {
    throw new Error("sealed renderer index must be a regular in-root file");
  }
  return real;
}

function requestSegments(request: IncomingMessage): string[] | null {
  const rawUrl = request.url;
  if (typeof rawUrl !== "string" || !rawUrl.startsWith("/") || rawUrl.startsWith("//")) return null;
  const rawPath = rawUrl.split(/[?#]/, 1)[0] ?? "";
  if (rawPath === "/") return [];
  if (rawPath.includes("\\")) return null;
  const encodedSegments = rawPath.slice(1).split("/");
  if (encodedSegments.some((part) => part.length === 0)) return null;
  const segments: string[] = [];
  for (const encoded of encodedSegments) {
    let decoded: string;
    try { decoded = decodeURIComponent(encoded); }
    catch { return null; }
    if (
      decoded === "." || decoded === ".." ||
      /[\\/\u0000-\u001f\u007f:*?"<>|]/.test(decoded) || /[. ]$/.test(decoded)
    ) return null;
    segments.push(decoded);
  }
  return segments;
}

function sendText(response: ServerResponse, status: number, body: string, extra: Record<string, string> = {}): void {
  response.writeHead(status, { "Content-Type": "text/plain; charset=utf-8", "X-Content-Type-Options": "nosniff", ...extra });
  response.end(body);
}

function serveRequest(root: string, request: IncomingMessage, response: ServerResponse): void {
  if (request.method !== "GET" && request.method !== "HEAD") {
    sendText(response, 405, "Method Not Allowed", { Allow: "GET, HEAD" });
    return;
  }
  const segments = requestSegments(request);
  if (!segments) {
    sendText(response, 400, "Bad Request");
    return;
  }
  const relativeSegments = segments.length ? segments : ["index.html"];
  let target = root;
  for (let index = 0; index < relativeSegments.length; index += 1) {
    target = path.join(target, relativeSegments[index]);
    let info;
    try { info = lstatSync(target); }
    catch { sendText(response, 404, "Not Found"); return; }
    if (info.isSymbolicLink() || (index < relativeSegments.length - 1 ? !info.isDirectory() : !info.isFile())) {
      sendText(response, 404, "Not Found");
      return;
    }
    let real: string;
    try { real = realpathSync.native(target); }
    catch { sendText(response, 404, "Not Found"); return; }
    if (!samePath(real, target) || (real !== root && !inside(root, real))) {
      sendText(response, 404, "Not Found");
      return;
    }
  }
  let body: Buffer;
  try { body = readFileSync(target); }
  catch { sendText(response, 404, "Not Found"); return; }
  response.writeHead(200, {
    "Content-Type": CONTENT_TYPES[path.extname(target).toLowerCase()] ?? "application/octet-stream",
    "Content-Length": String(body.length),
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
  });
  response.end(request.method === "HEAD" ? undefined : body);
}

/** Serve only an H1 sealed renderer tree, on a private loopback port selected by the OS. */
export async function startEvolutionTestRendererServer(distRoot: string): Promise<EvolutionTestRendererServer> {
  const root = trustedDirectory(distRoot);
  const server = createServer((request, response) => serveRequest(root, request, response));
  await new Promise<void>((resolve, reject) => {
    const onError = (error: Error) => { server.off("listening", onListening); reject(error); };
    const onListening = () => { server.off("error", onError); resolve(); };
    server.once("error", onError);
    server.once("listening", onListening);
    server.listen(0, LOOPBACK);
  });
  const address = server.address();
  if (!address || typeof address === "string" || address.port === 0 || address.address !== LOOPBACK) {
    await new Promise<void>((resolve) => server.close(() => resolve()));
    throw new Error("sealed renderer did not bind a dynamic loopback port");
  }
  const origin = `http://${LOOPBACK}:${address.port}`;
  let closePromise: Promise<void> | null = null;
  return {
    origin,
    indexUrl: `${origin}/`,
    port: address.port,
    close() {
      if (!closePromise) {
        closePromise = new Promise<void>((resolve, reject) => {
          server.close((error) => error ? reject(error) : resolve());
          server.closeAllConnections();
        });
      }
      return closePromise;
    },
  };
}
