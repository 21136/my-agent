export type MemoryShelfMemory = {
  memory_id: string;
  topics: string[];
  summary: string;
  path: string;
};

export type MemoryShelfPending = {
  proposal_id: string;
  type: string;
  mode: string;
  summary: string;
  target_path?: string | null;
  topics: string[];
};

export function memoryShelfLabel(state: {
  memories: MemoryShelfMemory[];
  pending: MemoryShelfPending[];
}): string {
  const n = state.memories.length;
  const p = state.pending.length;
  if (n === 0 && p === 0) return "记忆";
  if (p > 0) return `记忆 ${n} · 待审 ${p}`;
  return `记忆 ${n}`;
}
