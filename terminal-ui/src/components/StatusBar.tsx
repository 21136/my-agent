import React, {useEffect, useState} from 'react';
import {Box, Text} from 'ink';
import {tokens} from '../theme/tokens.js';
import {formatToolElapsed} from './formatToolElapsed.js';
import type {TerminalResult} from '../types.js';

type Props = {
  model: string;
  root: string;
  working?: boolean;
  toolName?: string;
  toolStartedAt?: number;
  planStatus?: string;
  result?: TerminalResult;
  columns?: number;
};

export function truncateStatusText(value: string, maxLength: number): string {
  if (maxLength <= 0) return '';
  if (value.length <= maxLength) return value;
  if (maxLength <= 3) return value.slice(0, maxLength);
  const headLength = Math.ceil((maxLength - 1) / 2);
  const tailLength = Math.floor((maxLength - 1) / 2);
  return `${value.slice(0, headLength)}…${value.slice(-tailLength)}`;
}

function useElapsedSeconds(startedAt: number | undefined, active: boolean): number | undefined {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!active || startedAt === undefined) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [active, startedAt]);

  if (!active || startedAt === undefined) return undefined;
  return Math.floor((now - startedAt) / 1000);
}

export function StatusBar({model, root, working, toolName, toolStartedAt, planStatus, result, columns = 80}: Props) {
  const s = tokens.status;
  const elapsedSeconds = useElapsedSeconds(toolStartedAt, Boolean(working && toolName));
  const minimal = columns < 40;
  const compact = columns < 60;
  const activity = working
    ? `${toolName ? `◐ ${toolName}` : '◐ working'}${elapsedSeconds !== undefined ? ` · ${formatToolElapsed(elapsedSeconds)}` : ''}`
    : '● idle';
  const displayRoot = truncateStatusText(root, minimal ? 16 : compact ? 24 : Math.max(24, columns - 48));
  const displayPlan = truncateStatusText(planStatus ?? '', compact ? 18 : 28);
  const resultText = result
    ? result.kind === 'failed'
      ? `! ${truncateStatusText(result.text, compact ? 18 : 30)}`
      : '× 已取消'
    : '';

  if (minimal) {
    return (
      <Box marginTop={0} width="100%">
        <Text color={result ? (result.kind === 'failed' ? s.failed : s.cancelled) : working ? s.working : s.muted}>
          {resultText || activity}
        </Text>
      </Box>
    );
  }

  return (
    <Box marginTop={0} width="100%">
      <Text color={s.muted}>
        <Text bold color={s.model}>
          {truncateStatusText(model, 16)}
        </Text>
        <Text color={s.sep}> · </Text>
        <Text bold color={s.root}>
          {displayRoot}
        </Text>
        {displayPlan ? (
          <>
            <Text color={s.sep}> · </Text>
            <Text color={s.working}>{displayPlan}</Text>
          </>
        ) : null}
        <Text color={s.sep}> · </Text>
        <Text color={result ? (result.kind === 'failed' ? s.failed : s.cancelled) : working ? s.working : s.muted}>
          {resultText || activity}
        </Text>
      </Text>
    </Box>
  );
}
