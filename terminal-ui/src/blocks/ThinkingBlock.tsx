import React from 'react';
import {Box, Text} from 'ink';
import {
  formatThinkingLines,
} from '../render/display-text.js';
import {tokens} from '../theme/tokens.js';

type Props = {
  text: string;
  columns?: number;
  active?: boolean;
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

function buildThinkingRows(text: string, columns: number, active: boolean): string[] {
  const t = tokens.transcript;
  const {top, bottom} = thinkingFrame(columns);
  const lines = formatThinkingLines(text, columns);
  const rows: string[] = [top];
  if (lines.length === 0) {
    rows.push(active ? '│ 思考中…' : '│');
  } else {
    for (const line of lines) {
      rows.push(line ? `│ ${line}` : '│');
    }
    if (active) rows.push('│ 思考中…');
  }
  rows.push(bottom);
  return rows;
}

export function ThinkingBlock({
  text,
  columns = 80,
  active = false,
  skipRows = 0,
  maxRows,
}: Props) {
  const t = tokens.transcript;
  const rows = buildThinkingRows(text, columns, active);
  if (rows.length === 0 && !active) return null;

  const sliceEnd = maxRows ?? rows.length;
  const visible = rows.slice(skipRows, skipRows + sliceEnd);

  if (visible.length === 0) return null;

  return (
    <Box width="100%" marginBottom={1} flexDirection="column" flexShrink={0}>
      {visible.map((line, index) => {
        const isLabel = line.startsWith('╭') || line.startsWith('╰');
        const isActive = line.includes('思考中');
        return (
          <Text
            key={index}
            italic={!isLabel}
            wrap="wrap"
            color={isLabel ? t.thinkingLabel : isActive ? t.thinkingLabel : t.thinkingText}
          >
            {line}
          </Text>
        );
      })}
    </Box>
  );
}
