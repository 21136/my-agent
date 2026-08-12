import React from 'react';
import {Box, Text} from 'ink';
import {truncateDisplayLine} from '../render/display-text.js';
import {tokens} from '../theme/tokens.js';

const UI_VERSION = '0.3.2';

type Props = {
  greet: string;
  model: string;
  root: string;
  columns?: number;
};

function padLine(text: string, width: number): string {
  const clipped = truncateDisplayLine(text, width);
  if (clipped.length >= width) return clipped;
  return clipped + ' '.repeat(width - clipped.length);
}

export function WelcomeCompact({greet, model, root, columns = 80}: Props) {
  const w = tokens.welcome;
  const inner = Math.max(24, columns - 4);
  const titleLeft = ' my-agent ';
  const titleRight = ` v${UI_VERSION} `;
  const titleFill = Math.max(1, inner - titleLeft.length - titleRight.length);
  const body = `  ${greet}  ·  ${model}  ·  ${root}  `;

  return (
    <Box width="100%" marginBottom={1} flexDirection="column" flexShrink={0}>
      <Text color={w.border}>
        {'╭'}
        {titleLeft}
        {'─'.repeat(titleFill)}
        {titleRight}
        {'╮'}
      </Text>
      <Text color={w.border}>
        {'│'}
        <Text bold color={w.greet}>
          {padLine(body, inner)}
        </Text>
        {'│'}
      </Text>
      <Text color={w.border}>{'╰' + '─'.repeat(inner) + '╯'}</Text>
    </Box>
  );
}
