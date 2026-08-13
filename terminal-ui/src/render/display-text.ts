/** Strip lightweight markdown — mirrors agent-core terminal_ui._strip_inline_markdown. */
export function stripInlineMarkdown(text: string): string {
  return text
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '$1 ($2)')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/\*([^*]+)\*/g, '$1')
    .replace(/`([^`]+)`/g, '$1');
}

export function truncateDisplayLine(text: string, maxLen: number): string {
  if (text.length <= maxLen) return text;
  if (maxLen <= 1) return '…';
  return `${text.slice(0, maxLen - 1)}…`;
}

/** Soft-wrap a single long line for terminal display (no ellipsis mid-thought). */
export function wrapDisplayLine(text: string, maxLen: number): string[] {
  if (maxLen <= 0) return [text];
  if (text.length <= maxLen) return [text];
  const out: string[] = [];
  let rest = text;
  while (rest.length > maxLen) {
    let breakAt = rest.lastIndexOf(' ', maxLen + 1);
    if (breakAt <= 0) breakAt = maxLen;
    const chunk = rest.slice(0, breakAt).trimEnd();
    if (chunk) out.push(chunk);
    rest = rest.slice(breakAt).trimStart();
  }
  if (rest) out.push(rest);
  return out.length > 0 ? out : [''];
}

/** Reasoning lines for terminal — plain text, width-wrapped per line. */
export function formatThinkingLines(text: string, columns: number): string[] {
  const width = Math.max(40, columns);
  const maxLine = Math.max(16, width - 10);
  const body = stripInlineMarkdown(text.trim());
  if (!body) return [];
  const out: string[] = [];
  for (const raw of body.split('\n')) {
    const line = raw.trimEnd();
    if (!line.trim()) {
      out.push('');
      continue;
    }
    out.push(...wrapDisplayLine(line, maxLine));
  }
  return out;
}

/** Default visible wrapped rows while reasoning is still streaming. */
export const STREAMING_THINKING_WRAPPED_LINES = 8;
/** Logical newline tail before wrap — keeps hot-path work bounded. */
export const STREAMING_THINKING_LOGICAL_LINES = 6;

/**
 * Bounded wrap for live reasoning — only tails recent logical lines, then caps
 * wrapped rows so Ink does not re-layout the entire trace on every delta.
 */
export function formatStreamingThinkingLines(
  text: string,
  columns: number,
  maxWrappedLines = STREAMING_THINKING_WRAPPED_LINES,
): {lines: string[]; clippedTop: boolean} {
  const body = stripInlineMarkdown(text.trim());
  if (!body) return {lines: [], clippedTop: false};

  const {text: tailText, clipped: logicalClipped} = tailLines(
    body,
    STREAMING_THINKING_LOGICAL_LINES,
  );
  const wrapped = formatThinkingLines(tailText, columns);
  const wrappedClipped = wrapped.length > maxWrappedLines;
  const lines = wrappedClipped ? wrapped.slice(-maxWrappedLines) : wrapped;
  return {lines, clippedTop: logicalClipped || wrappedClipped};
}

export function tailLines(
  text: string,
  maxLines: number,
): {text: string; clipped: boolean} {
  if (maxLines <= 0) return {text: '', clipped: text.length > 0};
  const lines = text.split('\n');
  if (lines.length <= maxLines) return {text, clipped: false};
  return {text: lines.slice(-maxLines).join('\n'), clipped: true};
}

export function wrapLineCount(text: string, columns: number, prefix = 0): number {
  const lines = expandWrappedLines(text, columns, prefix);
  return lines.length > 0 ? lines.length : 1;
}

/** Expand logical newlines into wrapped terminal rows. */
export function expandWrappedLines(text: string, columns: number, prefix = 0): string[] {
  const width = Math.max(20, columns - prefix);
  const out: string[] = [];
  for (const raw of text.split('\n')) {
    if (!raw) {
      out.push('');
      continue;
    }
    out.push(...wrapDisplayLine(raw, width));
  }
  return out;
}

export function sliceTextByWrappedRows(
  text: string,
  columns: number,
  skipRows: number,
  maxRows: number,
  prefix = 0,
): {text: string; clippedTop: boolean; clippedBottom: boolean} {
  const lines = expandWrappedLines(text, columns, prefix);
  if (maxRows <= 0) {
    return {text: '', clippedTop: skipRows > 0, clippedBottom: skipRows < lines.length};
  }
  const clippedTop = skipRows > 0;
  const clippedBottom = skipRows + maxRows < lines.length;
  return {
    text: lines.slice(skipRows, skipRows + maxRows).join('\n'),
    clippedTop,
    clippedBottom,
  };
}
