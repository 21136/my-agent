export type ManagedReviewSectionId = "stable-preservation" | "sandbox-ownership" | "history" | "effective-delta" | "excluded" | "blockers" | "commits" | "cross-lane";

export type ManagedReviewRow = { key: string; path: string; summary: string; details: string };
export type ManagedReviewSection = { id: ManagedReviewSectionId; label: string; rows: ManagedReviewRow[] };
export type ManagedReviewCatalog = {
  reviewDigest: string;
  baselines: string[];
  limitations: string[];
  sections: ManagedReviewSection[];
  execution: false;
  readyForMerge: false;
  readyForLaunch: false;
};
export type ManagedReviewReport = Record<string, unknown>;
export type ManagedReviewPageOptions = { sectionId: ManagedReviewSectionId; query?: string; page?: number; pageSize?: number };
export type ManagedReviewPage = { rows: ManagedReviewRow[]; total: number; page: number; pageCount: number };

const HASH = /^[a-f0-9]{64}$/i;
const OID = /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/i;
const labels: Record<ManagedReviewSectionId, string> = {
  "stable-preservation": "稳定区保留审查", "sandbox-ownership": "sandbox 归属审查", history: "历史触及路径",
  "effective-delta": "当前净树差异", excluded: "排除路径（仅元数据）", blockers: "阻塞项", commits: "提交元数据", "cross-lane": "两区逐路径对照",
};
const isRecord = (value: unknown): value is Record<string, any> => !!value && typeof value === "object" && !Array.isArray(value);
const requireArray = (value: unknown, name: string): any[] => { if (!Array.isArray(value)) throw new TypeError(`managed review ${name} inventory is missing or malformed`); return value; };
const json = (value: unknown): string => JSON.stringify(value);
const row = (key: string, path: string, summary: string, details: unknown): ManagedReviewRow => ({ key, path, summary, details: typeof details === "string" ? details : json(details) });

export function createManagedReviewCatalog(report: ManagedReviewReport): ManagedReviewCatalog {
  if (!isRecord(report) || report.format !== 1 || report.mode !== "managed_source_review_v1" || typeof report.review_digest !== "string" || !HASH.test(report.review_digest)) throw new TypeError("managed review report identity or digest is invalid");
  if (report.execution !== false || report.readyForMerge !== false || report.readyForLaunch !== false) throw new TypeError("managed review safety readiness contract is invalid");
  const stable = report.stable; const sandbox = report.sandbox;
  if (!isRecord(stable) || !isRecord(sandbox) || !isRecord(stable.inventory) || !isRecord(sandbox.inventory)) throw new TypeError("managed review inventory is missing");
  const stableEntries = requireArray(stable.inventory.entries, "stable entries");
  const sandboxEntries = requireArray(sandbox.inventory.entries, "sandbox entries");
  const stableExcluded = requireArray(stable.inventory.excluded, "stable excluded");
  const sandboxExcluded = requireArray(sandbox.inventory.excluded, "sandbox excluded");
  const stableLimits = requireArray(stable.inventory.limitations, "stable limitations");
  const sandboxLimits = requireArray(sandbox.inventory.limitations, "sandbox limitations");
  const history = report.history; const delta = report.effectiveDelta;
  if (!isRecord(history) || !isRecord(delta) || delta.basis !== "merge_base_to_sandbox_head") throw new TypeError("managed review history or effective-delta basis is invalid");
  const historyPaths = requireArray(history.paths, "history paths");
  const commits = requireArray(history.commits, "history commits");
  const deltaPaths = requireArray(delta.paths, "effective delta paths");
  const blockers = requireArray(report.blockers, "blockers");
  const limitations = requireArray(report.limitations, "limitations");
  const validEntry = (entry: unknown) => isRecord(entry) && typeof entry.path === "string" && entry.path.length > 0 && typeof entry.state === "string" && typeof entry.staged === "boolean" && typeof entry.rawDiverged === "boolean" && typeof entry.modeDiverged === "boolean";
  if (![...stableEntries, ...sandboxEntries].every(validEntry)) throw new TypeError("managed review entry is malformed");
  if (![...historyPaths, ...deltaPaths].every(x => isRecord(x) && typeof x.path === "string" && x.path.length > 0)) throw new TypeError("managed review path metadata is malformed");
  if (!commits.every(x => isRecord(x) && typeof x.oid === "string" && OID.test(x.oid) && Array.isArray(x.parents) && x.parents.every((p: unknown) => typeof p === "string" && OID.test(p)) && typeof x.tree === "string" && OID.test(x.tree))) throw new TypeError("managed review commit metadata is malformed");
  if (![...stableExcluded, ...sandboxExcluded].every(x => isRecord(x) && typeof x.path === "string" && typeof x.classification === "string")) throw new TypeError("managed review exclusion metadata is malformed");
  if (!blockers.every(x => isRecord(x) && typeof x.code === "string" && Array.isArray(x.paths) && x.paths.every((p: unknown) => typeof p === "string"))) throw new TypeError("managed review blockers are malformed");
  if (![...stableLimits, ...sandboxLimits, ...limitations].every(x => typeof x === "string")) throw new TypeError("managed review limitations are malformed");

  const inventoryRows = (entries: any[], lane: string) => entries.filter(e => e.state !== "clean" || e.staged || e.rawDiverged || e.modeDiverged).map(e => {
    const flags = [e.staged && "staged", e.rawDiverged && "raw local bytes differ (working SHA-256 vs Git blob identity)", e.modeDiverged && "modeDiverged"].filter(Boolean).join(", ") || "no flags";
    return row(`${lane}:${e.path}`, e.path, `${e.state}; ${flags}`, e);
  });
  const excludedRows = (items: any[], lane: string) => items.map(e => row(`${lane}:${e.path}`, e.path, `${lane}: ${e.classification}`, { path: e.path, classification: e.classification }));
  const historyRows = historyPaths.map((item: any) => row(item.path, item.path, `历史涉及 ${Array.isArray(item.commits) ? item.commits.length : 0} 次`, { commits: item.commits, renamePairs: item.renamePairs, allowed: item.allowed, protected: item.protected }));
  const deltaRows = deltaPaths.map((item: any) => row(item.path, item.path, `merge-base to sandbox HEAD · ${String(item.status ?? "changed")}`, item));
  const blockerRows = blockers.flatMap((item: any, index: number) => item.paths.length ? item.paths.map((p: string, pathIndex: number) => row(`${index}:${pathIndex}:${p}`, p, item.code, { code: item.code, path: p, blockerIndex: index, totalPaths: item.paths.length })) : [row(`${index}:`, "", item.code, { code: item.code, blockerIndex: index, totalPaths: 0 })]);
  const laneEntries = new Map<string, { stable: any | null; sandbox: any | null }>();
  for (const entry of stableEntries) laneEntries.set(entry.path, { stable: entry, sandbox: null });
  for (const entry of sandboxEntries) {
    const pair = laneEntries.get(entry.path) ?? { stable: null, sandbox: null };
    pair.sandbox = entry;
    laneEntries.set(entry.path, pair);
  }
  const crossLaneRows = [...laneEntries.entries()].sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0).map(([path, pair]) => {
    const stableHash = pair.stable?.workingSha256;
    const sandboxHash = pair.sandbox?.workingSha256;
    const validStableHash = typeof stableHash === "string" && HASH.test(stableHash);
    const validSandboxHash = typeof sandboxHash === "string" && HASH.test(sandboxHash);
    const comparison = validStableHash && validSandboxHash ? (stableHash.toLowerCase() === sandboxHash.toLowerCase() ? "same" : "different") : "unknown";
    const rawLabel = comparison === "same" ? "原始字节相同" : comparison === "different" ? "原始字节不同" : "原始字节未知";
    const sideNotes = ([ ["stable", pair.stable], ["sandbox", pair.sandbox] ] as const).flatMap(([side, entry]) => {
      if (entry === null) return [`${side}清单无此条目`];
      if (entry.workingSha256 === null) return [`${side}工作文件缺失`];
      if (typeof entry.workingSha256 !== "string" || !HASH.test(entry.workingSha256)) return [`${side}原始 SHA-256未知`];
      return [];
    });
    return row(path, path, [rawLabel, ...sideNotes].join("；"), { comparison, stable: pair.stable, sandbox: pair.sandbox });
  });
  const commitRows = commits.map((item: any) => row(item.oid, "", item.subject || "commit", item));
  const sections: ManagedReviewSection[] = [
    { id: "stable-preservation", label: labels["stable-preservation"], rows: inventoryRows(stableEntries, "stable") },
    { id: "sandbox-ownership", label: labels["sandbox-ownership"], rows: inventoryRows(sandboxEntries, "sandbox") },
    { id: "history", label: labels.history, rows: historyRows },
    { id: "effective-delta", label: labels["effective-delta"], rows: deltaRows },
    { id: "excluded", label: labels.excluded, rows: [...excludedRows(stableExcluded, "stable"), ...excludedRows(sandboxExcluded, "sandbox")] },
    { id: "blockers", label: labels.blockers, rows: blockerRows },
    { id: "commits", label: labels.commits, rows: commitRows },
    { id: "cross-lane", label: labels["cross-lane"], rows: crossLaneRows },
  ];
  const baselineStrings = [stable, sandbox].flatMap((lane, i) => {
    const name = i === 0 ? "stable" : "sandbox"; const ident = lane.identity;
    return [
      typeof lane.head === "string" ? `${name} HEAD: ${lane.head}` : undefined,
      typeof lane.branch === "string" ? `${name} branch: ${lane.branch}` : undefined,
      typeof lane.indexDigest === "string" ? `${name} index SHA256: ${lane.indexDigest}` : undefined,
      typeof ident?.worktreeRoot === "string" ? `${name} root: ${ident.worktreeRoot}` : undefined,
    ].filter((value): value is string => value !== undefined);
  });  return { reviewDigest: report.review_digest, baselines: baselineStrings, limitations: [...new Set([...stableLimits, ...sandboxLimits, ...limitations])], sections, execution: false, readyForMerge: false, readyForLaunch: false };
}

export function paginateManagedReviewCatalog(catalog: ManagedReviewCatalog, options: ManagedReviewPageOptions): ManagedReviewPage {
  const section = catalog.sections.find(item => item.id === options.sectionId);
  if (!section) throw new TypeError("unknown managed review section");
  const query = typeof options.query === "string" ? options.query.toLocaleLowerCase() : "";
  const filtered = section.rows.filter(item => item.path.toLocaleLowerCase().includes(query) || item.summary.toLocaleLowerCase().includes(query));
  const requestedSize = Number.isFinite(options.pageSize) ? Math.trunc(options.pageSize!) : 25;
  const pageSize = Math.max(1, Math.min(100, requestedSize));
  const pageCount = Math.ceil(filtered.length / pageSize);
  const requestedPage = Number.isFinite(options.page) ? Math.trunc(options.page!) : 1;
  const page = pageCount === 0 ? 1 : Math.max(1, Math.min(pageCount, requestedPage));
  return { rows: filtered.slice((page - 1) * pageSize, page * pageSize), total: filtered.length, page, pageCount };
}



