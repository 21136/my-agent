const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost", "::1"]);

/** Happy-path policy: only loopback HTTP(S) on the configured dev origin. */
export function normalizeShellDevPreviewBase(raw: string): string | null {
  const text = raw.trim();
  if (!text) return null;
  try {
    const url = new URL(text);
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    if (url.username || url.password) return null;
    if (!LOOPBACK_HOSTS.has(url.hostname.toLowerCase())) return null;
    url.hash = "";
    url.search = "";
    if (!url.pathname.endsWith("/")) url.pathname += "/";
    return url.toString();
  } catch {
    return null;
  }
}

/** Main workbench renderer (stable lane) while developing. */
export function defaultShellMainDevBase(): string {
  const fromEnv = normalizeShellDevPreviewBase(process.env.VITE_DEV_SERVER_URL || "");
  if (fromEnv) return fromEnv;
  return "http://127.0.0.1:5173/";
}

/** Candidate shell lane — defaults to main port + 1 so two Vite servers can run together. */
export function deriveCandidatePreviewUrl(mainBase: string): string {
  try {
    const main = new URL(mainBase);
    const port = Number(main.port || (main.protocol === "https:" ? 443 : 80));
    const next = port > 0 && port < 65535 ? port + 1 : 5174;
    main.port = String(next);
    main.hash = "";
    main.search = "";
    if (!main.pathname.endsWith("/")) main.pathname += "/";
    return main.toString();
  } catch {
    return "http://127.0.0.1:5174/";
  }
}

export function defaultShellDevPreviewBase(): string {
  const explicit = normalizeShellDevPreviewBase(process.env.MY_AGENT_SHELL_PREVIEW_URL || "");
  if (explicit) return explicit;
  return deriveCandidatePreviewUrl(defaultShellMainDevBase());
}

function loopbackOriginsEquivalent(a: URL, b: URL): boolean {
  if (a.protocol !== b.protocol) return false;
  const portA = a.port || (a.protocol === "https:" ? "443" : "80");
  const portB = b.port || (b.protocol === "https:" ? "443" : "80");
  if (portA !== portB) return false;
  const hostA = a.hostname.toLowerCase();
  const hostB = b.hostname.toLowerCase();
  const loopback = (h: string) => h === "127.0.0.1" || h === "localhost" || h === "::1";
  if (loopback(hostA) && loopback(hostB)) return true;
  return hostA === hostB;
}

export function isAllowedShellDevPreviewNavigation(url: string, baseUrl: string): boolean {
  const base = normalizeShellDevPreviewBase(baseUrl);
  if (!base) return false;
  try {
    const target = new URL(url.trim());
    const origin = new URL(base);
    if (target.protocol !== "http:" && target.protocol !== "https:") return false;
    if (target.username || target.password) return false;
    if (!LOOPBACK_HOSTS.has(target.hostname.toLowerCase())) return false;
    return loopbackOriginsEquivalent(target, origin);
  } catch {
    return false;
  }
}

export function configuredShellDevPreviewBase(): string {
  return defaultShellDevPreviewBase();
}

/** Try localhost and 127.0.0.1 — Vite may listen on only one hostname. */
export function loopbackUrlVariants(base: string): string[] {
  const norm = normalizeShellDevPreviewBase(base);
  if (!norm) return [];
  const url = new URL(norm);
  const out = new Set<string>([norm]);
  const host = url.hostname.toLowerCase();
  if (host === "127.0.0.1") {
    url.hostname = "localhost";
    out.add(url.toString());
  } else if (host === "localhost") {
    url.hostname = "127.0.0.1";
    out.add(url.toString());
  }
  return [...out];
}

/** Promotion only accepts loopback dev renderer URLs. */
export function isAllowedShellRendererPromotion(url: string): boolean {
  return Boolean(normalizeShellDevPreviewBase(url));
}
