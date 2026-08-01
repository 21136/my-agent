/**
 * Global constellation persistence — data/constellation.json (T-904g7).
 */

export interface ConstellationStar {
  id: string;
  x: number;
  y: number;
  session_id: string;
  turn_index: number;
  role: "user" | "assistant";
  created_at: string;
}

export interface ConstellationLink {
  from: string;
  to: string;
  session_id: string;
  turn_index: number;
}

export interface ConstellationFile {
  version: 1;
  stars: ConstellationStar[];
  links: ConstellationLink[];
}

const MAX_PAIRS = 80;

function starKey(sessionId: string, turnIndex: number, role: "user" | "assistant"): string {
  return `${sessionId}:${turnIndex}:${role}`;
}

function pairKey(sessionId: string, turnIndex: number): string {
  return `${sessionId}:${turnIndex}`;
}

function isStar(value: unknown): value is ConstellationStar {
  if (!value || typeof value !== "object") return false;
  const row = value as Record<string, unknown>;
  return (
    typeof row.id === "string" &&
    typeof row.x === "number" &&
    typeof row.y === "number" &&
    typeof row.session_id === "string" &&
    typeof row.turn_index === "number" &&
    (row.role === "user" || row.role === "assistant") &&
    typeof row.created_at === "string"
  );
}

function isLink(value: unknown): value is ConstellationLink {
  if (!value || typeof value !== "object") return false;
  const row = value as Record<string, unknown>;
  return (
    typeof row.from === "string" &&
    typeof row.to === "string" &&
    typeof row.session_id === "string" &&
    typeof row.turn_index === "number"
  );
}

function normalizeFile(raw: { version?: number; stars?: unknown[]; links?: unknown[] }): ConstellationFile {
  return {
    version: 1,
    stars: (raw.stars ?? []).filter(isStar),
    links: (raw.links ?? []).filter(isLink),
  };
}

function trimToPairLimit(file: ConstellationFile): ConstellationFile {
  const pairMap = new Map<string, { createdAt: string; stars: ConstellationStar[] }>();
  for (const star of file.stars) {
    const key = pairKey(star.session_id, star.turn_index);
    const existing = pairMap.get(key);
    if (!existing) {
      pairMap.set(key, { createdAt: star.created_at, stars: [star] });
      continue;
    }
    existing.stars.push(star);
    if (star.created_at < existing.createdAt) {
      existing.createdAt = star.created_at;
    }
  }

  const pairs = [...pairMap.entries()].sort((a, b) => a[1].createdAt.localeCompare(b[1].createdAt));
  if (pairs.length <= MAX_PAIRS) {
    return file;
  }

  const dropKeys = new Set(pairs.slice(0, pairs.length - MAX_PAIRS).map(([key]) => key));
  const stars = file.stars.filter((star) => !dropKeys.has(pairKey(star.session_id, star.turn_index)));
  const starIds = new Set(stars.map((star) => star.id));
  const links = file.links.filter(
    (link) =>
      !dropKeys.has(pairKey(link.session_id, link.turn_index)) &&
      starIds.has(link.from) &&
      starIds.has(link.to),
  );
  return { version: 1, stars, links };
}

async function readFile(): Promise<ConstellationFile> {
  const api = window.myAgentDesktop;
  if (!api?.readConstellation) {
    return { version: 1, stars: [], links: [] };
  }
  const raw = await api.readConstellation();
  return trimToPairLimit(normalizeFile(raw));
}

async function writeFile(file: ConstellationFile): Promise<void> {
  const api = window.myAgentDesktop;
  if (!api?.writeConstellation) return;
  const trimmed = trimToPairLimit(file);
  await api.writeConstellation(trimmed);
}

export interface ConstellationStore {
  load(): Promise<ConstellationFile>;
  appendStar(star: ConstellationStar): Promise<void>;
  appendLink(link: ConstellationLink): Promise<void>;
  clear(): Promise<void>;
}

export function createConstellationStore(): ConstellationStore {
  let writeChain: Promise<void> = Promise.resolve();

  function enqueueWrite(task: () => Promise<void>): Promise<void> {
    writeChain = writeChain.then(task, task);
    return writeChain;
  }

  return {
    async load(): Promise<ConstellationFile> {
      return readFile();
    },

    appendStar(star: ConstellationStar): Promise<void> {
      return enqueueWrite(async () => {
        const file = await readFile();
        const key = starKey(star.session_id, star.turn_index, star.role);
        if (file.stars.some((row) => starKey(row.session_id, row.turn_index, row.role) === key)) {
          return;
        }
        file.stars.push(star);
        await writeFile(file);
      });
    },

    appendLink(link: ConstellationLink): Promise<void> {
      return enqueueWrite(async () => {
        const file = await readFile();
        if (
          file.links.some(
            (row) =>
              row.session_id === link.session_id &&
              row.turn_index === link.turn_index &&
              row.from === link.from &&
              row.to === link.to,
          )
        ) {
          return;
        }
        file.links.push(link);
        await writeFile(file);
      });
    },

    clear(): Promise<void> {
      return enqueueWrite(async () => {
        const api = window.myAgentDesktop;
        if (!api?.clearConstellation) return;
        await api.clearConstellation();
      });
    },
  };
}

export function newStarId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return `star-${crypto.randomUUID()}`;
  }
  return `star-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}
