import type {TerminalBlock} from '../types.js';

export type LiveStripOptions = {
  stripActiveThinking?: boolean;
  stripStreamingAssistant?: boolean;
};

export const EPHEMERAL_NOTICE_MS = 5000;

const EPHEMERAL_PLAN_NOTICE_RE = /^\[Terminal\] auto-plan (判定|规划中)/;

export function isEphemeralPlanNotice(text: string): boolean {
  return EPHEMERAL_PLAN_NOTICE_RE.test(text.trim());
}

export function filterExpiredNotices(
  blocks: readonly TerminalBlock[],
  now: number,
  ttlMs = EPHEMERAL_NOTICE_MS,
): TerminalBlock[] {
  return blocks.filter((block) => {
    if (block.kind !== 'notice' || !block.ephemeral) return true;
    const shownAt = block.shownAt ?? 0;
    return now - shownAt < ttlMs;
  });
}

/** Blocks that are already committed to the static transcript (live overlays render separately). */
export function committedTranscriptBlocks(
  blocks: readonly TerminalBlock[],
  options: LiveStripOptions,
): TerminalBlock[] {
  let end = blocks.length;
  if (options.stripStreamingAssistant) {
    const last = blocks[end - 1];
    if (last?.kind === 'assistant_streaming') end -= 1;
  }
  if (options.stripActiveThinking) {
    const last = blocks[end - 1];
    if (last?.kind === 'thinking' && !last.collapsed) end -= 1;
  }
  if (end === blocks.length) return [...blocks];
  return blocks.slice(0, end);
}

export function findLastThinkingIndex(blocks: readonly TerminalBlock[]): number {
  for (let i = blocks.length - 1; i >= 0; i -= 1) {
    if (blocks[i]?.kind === 'thinking') return i;
  }
  return -1;
}

export function trailingThinkingActive(
  blocks: readonly TerminalBlock[],
  working: boolean,
  liveReasoningText = '',
  planStatus = '',
): boolean {
  if (!working) return false;
  if (liveReasoningText.trim()) return true;
  const lastThinking = findLastThinkingIndex(blocks);
  if (lastThinking >= 0) {
    const block = blocks[lastThinking];
    if (block?.kind === 'thinking' && !block.collapsed) return true;
  }
  if (planStatus && !trailingAssistantStreaming(blocks, working, '')) return true;
  return false;
}

export function trailingAssistantStreaming(
  blocks: readonly TerminalBlock[],
  working: boolean,
  liveAssistantText: string,
): boolean {
  if (!working && !liveAssistantText) return false;
  if (liveAssistantText) return true;
  return blocks.at(-1)?.kind === 'assistant_streaming';
}
