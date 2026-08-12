import React, {useEffect, useState} from 'react';
import {Box, Text} from 'ink';
import {tokens} from '../theme/tokens.js';
import {formatToolElapsed} from './formatToolElapsed.js';

type Props = {
  model: string;
  root: string;
  working?: boolean;
  toolName?: string;
  toolStartedAt?: number;
};

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

export function StatusBar({model, root, working, toolName, toolStartedAt}: Props) {
  const s = tokens.status;
  const elapsedSeconds = useElapsedSeconds(toolStartedAt, Boolean(working && toolName));

  return (
    <Box marginTop={0} width="100%">
      <Text color={s.muted}>
        <Text bold color={s.model}>
          {model}
        </Text>
        <Text color={s.sep}> · </Text>
        <Text bold color={s.root}>
          {root}
        </Text>
        {working ? (
          <>
            <Text color={s.sep}> · </Text>
            <Text bold color={s.working}>
              ◐
            </Text>
            {toolName ? (
              <>
                <Text color={s.muted}> </Text>
                <Text color={s.toolName}>{toolName}</Text>
                {elapsedSeconds !== undefined ? (
                  <>
                    <Text color={s.sep}> · </Text>
                    <Text color={s.muted}>{formatToolElapsed(elapsedSeconds)}</Text>
                  </>
                ) : null}
              </>
            ) : null}
          </>
        ) : null}
      </Text>
    </Box>
  );
}
