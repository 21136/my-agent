import React from 'react';
import {Box, Text} from 'ink';

type LineTextProps = {
  text: string;
  color?: string;
  backgroundColor?: string;
  bold?: boolean;
  italic?: boolean;
};

/** Ink miscounts height for multiline Text with background + soft-wrap — one row per line. */
export function LineText({
  text,
  color,
  backgroundColor,
  bold,
  italic,
}: LineTextProps) {
  const lines = text.split('\n');
  return (
    <Box flexDirection="column" width="100%">
      {lines.map((line, index) => (
        <Text
          key={index}
          color={color}
          backgroundColor={backgroundColor}
          bold={bold}
          italic={italic}
          wrap="wrap"
        >
          {line || ' '}
        </Text>
      ))}
    </Box>
  );
}
