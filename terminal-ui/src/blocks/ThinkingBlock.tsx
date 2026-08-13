import React, {useEffect, useMemo, useState} from 'react';
import {Box, Text} from 'ink';
import {
  formatStreamingThinkingLines,
  formatThinkingLines,
  STREAMING_THINKING_WRAPPED_LINES,
} from '../render/display-text.js';
import {tokens} from '../theme/tokens.js';

type Props = {
  text: string;
  columns?: number;
  active?: boolean;
  collapsed?: boolean;
  skipRows?: number;
  maxRows?: number;
};

function thinkingFrame(columns: number): {top: string; bottom: string} {
  const inner = Math.max(24, columns - 4);
  const topLabel = '╭─ 思考 ';
  const dashCount = Math.max(0, inner - topLabel.length);
  return {
    top: topLabel + '─'.repeat(dashCount),
    bottom: '╰' + '─'.repeat(dashCount + 3),
  };
}

function activeThinkingLabel(tick: number): string {
  const dots = '.'.repeat((tick % 3) + 1);
  return `│ 思考中${dots}`;
}

function thinkingSummaryLine(text: string, columns: number): string {
  const lines = formatThinkingLines(text, columns).filter((line) => line.trim());
  return lines.at(-1) ?? lines[0] ?? '';
}

function buildThinkingBody(
  text: string,
  columns: number,
  active: boolean,
  collapsed: boolean,
  tick: number,
): string[] {
  const body = text.trim();
  if (collapsed && body) {
    const summary = thinkingSummaryLine(body, columns);
    return summary ? [`│ ${summary}`] : ['│'];
  }

  if (active && body) {
    const {lines, clippedTop} = formatStreamingThinkingLines(
      text,
      columns,
      STREAMING_THINKING_WRAPPED_LINES,
    );
    const rows: string[] = [];
    if (clippedTop) rows.push('│ …');
    for (const line of lines) {
      rows.push(line ? `│ ${line}` : '│');
    }
    return rows;
  }

  const lines = formatThinkingLines(text, columns);
  if (lines.length === 0) {
    return [active ? activeThinkingLabel(tick) : '│'];
  }
  const rows = lines.map((line) => (line ? `│ ${line}` : '│'));
  if (active && !body) rows.push(activeThinkingLabel(tick));
  return rows;
}

export function ThinkingBlock({
  text,
  columns = 80,
  active = false,
  collapsed = false,
  skipRows = 0,
  maxRows,
}: Props) {
  const t = tokens.transcript;
  const [tick, setTick] = useState(0);
  const showCollapsed = collapsed || (!active && text.trim());
  const {top, bottom} = useMemo(() => thinkingFrame(columns), [columns]);

  useEffect(() => {
    if (!active || text.trim()) return;
    const timer = setInterval(() => setTick((value) => value + 1), 450);
    return () => clearInterval(timer);
  }, [active, text]);

  const bodyRows = useMemo(
    () => buildThinkingBody(text, columns, active, showCollapsed, tick),
    [text, columns, active, showCollapsed, tick],
  );

  if (!active && !text.trim()) return null;
  if (bodyRows.length === 0 && !active) return null;

  const frameRows = showCollapsed ? [top, ...bodyRows, bottom] : [top, ...bodyRows, bottom];
  const sliceEnd = showCollapsed ? frameRows.length : maxRows ?? frameRows.length;
  const visible = frameRows.slice(skipRows, skipRows + sliceEnd);

  if (visible.length === 0) return null;

  if (active && !showCollapsed) {
    const body = visible.slice(1, -1).join('\n');
    return (
      <Box width="100%" marginBottom={1} flexDirection="column" flexShrink={0}>
        <Text color={t.thinkingLabel}>{visible[0]}</Text>
        {body ? (
          <Text italic color={t.thinkingText}>
            {body}
          </Text>
        ) : null}
        <Text color={t.thinkingLabel}>{visible.at(-1) ?? bottom}</Text>
      </Box>
    );
  }

  return (
    <Box width="100%" marginBottom={1} flexDirection="column" flexShrink={0}>
      {visible.map((line, index) => {
        const isLabel = line.startsWith('╭') || line.startsWith('╰');
        return (
          <Text
            key={index}
            italic={!isLabel}
            color={isLabel ? t.thinkingLabel : t.thinkingText}
          >
            {line}
          </Text>
        );
      })}
    </Box>
  );
}
