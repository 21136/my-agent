import React from 'react';
import {Box, Text} from 'ink';
import {tokens} from '../theme/tokens.js';

export function TurnSep() {
  return (
    <Box marginY={1}>
      <Text color={tokens.transcript.turnSep}>── 新回合 ──</Text>
    </Box>
  );
}
