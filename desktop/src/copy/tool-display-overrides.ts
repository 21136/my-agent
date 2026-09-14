/** User-customized tool display names (localStorage). LLM ids stay canonical. */

const STORAGE_KEY = "tool-display-overrides";

export type ToolDisplayOverrides = Record<string, string>;

const listeners = new Set<() => void>();

function notify(): void {
  for (const fn of listeners) fn();
}

export function onToolDisplayOverridesChange(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function readToolDisplayOverrides(): ToolDisplayOverrides {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== "object") return {};
    const out: ToolDisplayOverrides = {};
    for (const [key, value] of Object.entries(parsed)) {
      if (typeof value === "string" && value.trim()) out[key] = value.trim();
    }
    return out;
  } catch {
    return {};
  }
}

function writeAll(overrides: ToolDisplayOverrides): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(overrides));
  } catch {
    /* ignore quota */
  }
  notify();
}

export function getToolDisplayOverride(toolId: string): string | undefined {
  return readToolDisplayOverrides()[toolId.trim().toLowerCase()];
}

export function setToolDisplayOverride(toolId: string, label: string): void {
  const id = toolId.trim().toLowerCase();
  const text = label.trim();
  if (!id || !text) return;
  const all = readToolDisplayOverrides();
  all[id] = text;
  writeAll(all);
}

export function clearToolDisplayOverride(toolId: string): void {
  const id = toolId.trim().toLowerCase();
  const all = readToolDisplayOverrides();
  if (!(id in all)) return;
  delete all[id];
  writeAll(all);
}
