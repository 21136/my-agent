import type {TerminalBlock} from '../types.js';
import {
  expandWrappedLines,
  formatThinkingLines,
  stripInlineMarkdown,
  wrapLineCount,
} from '../render/display-text.js';

export const DEFAULT_VISIBLE_BLOCKS = 200;
export const MAX_THINKING_DISPLAY_LINES = 8;
/** @deprecated Assistant bodies are no longer tail-truncated; kept for tests. */
export const MAX_ASSISTANT_DISPLAY_LINES = 48;

export const ASSISTANT_HEADER_ROWS = 1;
export const USER_PREFIX_COLS = 4;

export type VisibleBlock = {
  block: TerminalBlock;
  index: number;
  skipRows: number;
  maxRows: number;
};

export type ViewportSlice = {
  entries: readonly VisibleBlock[];
  clippedTop: boolean;
  clippedBottom: boolean;
};

type BlockLayout = {block: TerminalBlock; index: number; rows: number};

export function getVisibleBlockEntries(
  blocks: readonly TerminalBlock[],
  maxBlocks = DEFAULT_VISIBLE_BLOCKS,
): readonly VisibleBlock[] {
  if (maxBlocks <= 0) return [];
  const start = Math.max(0, blocks.length - maxBlocks);
  return blocks
    .slice(start)
    .map((block, offset) => ({
      block,
      index: start + offset,
      skipRows: 0,
      maxRows: Number.MAX_SAFE_INTEGER,
    }));
}

export function getVisibleBlocks(
  blocks: readonly TerminalBlock[],
  maxBlocks = DEFAULT_VISIBLE_BLOCKS,
): readonly TerminalBlock[] {
  if (maxBlocks <= 0 || blocks.length <= maxBlocks) return blocks;
  return blocks.slice(-maxBlocks);
}

export function assistantBodyRows(body: string, columns: number): number {
  const stripped = body.trim();
  if (!stripped) return 0;
  let rows = 0;
  for (const raw of stripped.split('\n')) {
    const line = raw.trimEnd();
    if (!line) {
      rows += 1;
      continue;
    }
    if (/^#{1,6}\s/.test(line)) {
      rows += wrapLineCount(line.replace(/^#{1,6}\s+/, ''), columns);
      continue;
    }
    if (/^[-*]\s/.test(line) || /^\d+\.\s/.test(line)) {
      rows += wrapLineCount(line, columns, 2);
      continue;
    }
    rows += wrapLineCount(line, columns);
  }
  const plain = expandWrappedLines(stripInlineMarkdown(stripped), columns).length;
  return Math.max(rows, plain);
}

export function estimateBlockRows(block: TerminalBlock, columns: number): number {
  switch (block.kind) {
    case 'user':
      return expandWrappedLines(`❯ ${block.text}`, columns, USER_PREFIX_COLS).length;
    case 'thinking': {
      const lines = formatThinkingLines(block.text, columns);
      return 2 + Math.max(lines.length, 1);
    }
    case 'assistant':
    case 'assistant_streaming':
      return ASSISTANT_HEADER_ROWS + assistantBodyRows(block.body, columns);
    case 'notice':
      return wrapLineCount(block.text, columns, USER_PREFIX_COLS) + 1;
    case 'turn_sep':
      return 1;
    default:
      return 1;
  }
}

function buildBlockLayout(blocks: readonly TerminalBlock[], columns: number): BlockLayout[] {
  return blocks.map((block, index) => ({
    block,
    index,
    rows: estimateBlockRows(block, columns),
  }));
}

export function estimateTranscriptRows(
  blocks: readonly TerminalBlock[],
  columns: number,
): number {
  return buildBlockLayout(blocks, columns).reduce((sum, item) => sum + item.rows, 0);
}

export function maxTranscriptScrollUp(
  blocks: readonly TerminalBlock[],
  maxRows: number,
  columns: number,
): number {
  if (maxRows <= 0 || blocks.length === 0) return 0;
  return Math.max(0, estimateTranscriptRows(blocks, columns) - maxRows);
}

/** Fit transcript blocks into a row budget; scrollUpRows shifts the window toward older content. */
export function getViewportBlockEntries(
  blocks: readonly TerminalBlock[],
  maxRows: number,
  columns: number,
  scrollUpRows = 0,
): ViewportSlice {
  if (maxRows <= 0 || blocks.length === 0) {
    return {entries: [], clippedTop: false, clippedBottom: false};
  }

  const layout = buildBlockLayout(blocks, columns);
  const totalRows = layout.reduce((sum, item) => sum + item.rows, 0);

  if (totalRows <= maxRows && scrollUpRows === 0) {
    return {
      entries: layout.map((item) => ({
        block: item.block,
        index: item.index,
        skipRows: 0,
        maxRows: Number.MAX_SAFE_INTEGER,
      })),
      clippedTop: false,
      clippedBottom: false,
    };
  }

  if (scrollUpRows === 0) {
    const entries: VisibleBlock[] = [];
    let used = 0;
    for (let i = layout.length - 1; i >= 0; i -= 1) {
      const item = layout[i]!;
      entries.unshift({
        block: item.block,
        index: item.index,
        skipRows: 0,
        maxRows: Number.MAX_SAFE_INTEGER,
      });
      used += item.rows;
      if (i > 0 && used >= maxRows) break;
    }
    return {
      entries,
      clippedTop: entries.length > 0 && entries[0]!.index > 0,
      clippedBottom: false,
    };
  }

  const maxScrollUp = Math.max(0, totalRows - maxRows);
  const clampedScroll = Math.min(Math.max(0, scrollUpRows), maxScrollUp);
  const windowEnd = totalRows - clampedScroll;
  const windowStart = Math.max(0, windowEnd - maxRows);

  const entries: VisibleBlock[] = [];
  let rowCursor = 0;
  for (const item of layout) {
    const blockStart = rowCursor;
    const blockEnd = rowCursor + item.rows;
    rowCursor = blockEnd;
    if (blockEnd > windowStart && blockStart < windowEnd) {
      const skipRows = Math.max(0, windowStart - blockStart);
      const maxBlockRows = Math.min(blockEnd, windowEnd) - Math.max(blockStart, windowStart);
      const fullBlock = skipRows === 0 && maxBlockRows >= item.rows;
      entries.push({
        block: item.block,
        index: item.index,
        skipRows: fullBlock ? 0 : skipRows,
        maxRows: fullBlock ? Number.MAX_SAFE_INTEGER : maxBlockRows,
      });
    }
  }

  return {
    entries,
    clippedTop: windowStart > 0,
    clippedBottom: clampedScroll > 0,
  };
}

export function transcriptRowBudget(
  height: number | undefined,
  welcomeCompact: boolean,
): number {
  if (!height || height <= 0) return 24;
  const welcomeRows = welcomeCompact ? 3 : 12;
  const footerRows = 3;
  return Math.max(12, height - welcomeRows - footerRows);
}

export function scrollTranscriptRows(
  current: number,
  delta: number,
  maxScrollUp: number,
): number {
  if (delta === 0) return current;
  if (delta < 0) return Math.max(0, current + delta);
  return Math.min(maxScrollUp, current + delta);
}
