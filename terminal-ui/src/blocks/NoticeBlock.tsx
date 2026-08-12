import React from 'react';
import {Box, Text} from 'ink';
import {tokens} from '../theme/tokens.js';

type Props = {
  text: string;
};

export function NoticeBlock({text}: Props) {
  const t = tokens.transcript;
  return (
    <Box marginBottom={1} paddingX={1}>
      <Text>
        <Text bold color={t.noticeWarn}>
          │{' '}
        </Text>
        <Text bold color={t.noticeWarn}>
          {text}
        </Text>
      </Text>
    </Box>
  );
}
