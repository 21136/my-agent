import React from 'react';
import {Box, Text} from 'ink';
import {tokens} from '../theme/tokens.js';

type Props = {
  text: string;
  tone?: 'warning' | 'error' | 'cancelled';
};

export function NoticeBlock({text, tone = 'warning'}: Props) {
  const t = tokens.transcript;
  const color = tone === 'error' ? tokens.status.failed : tone === 'cancelled' ? tokens.status.cancelled : t.noticeWarn;
  const prefix = tone === 'error' ? '! ' : tone === 'cancelled' ? '× ' : '│ ';
  return (
    <Box marginBottom={1} paddingX={1}>
      <Text>
        <Text bold color={color}>
          {prefix}
        </Text>
        <Text bold color={color}>
          {text}
        </Text>
      </Text>
    </Box>
  );
}
