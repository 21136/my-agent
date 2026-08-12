import React from 'react';

import {Box, Text} from 'ink';

import {sliceTextByWrappedRows} from '../render/display-text.js';

import {USER_PREFIX_COLS} from '../perf/virtual-list.js';

import {tokens} from '../theme/tokens.js';



type Props = {

  text: string;

  columns?: number;

  skipRows?: number;

  maxRows?: number;

};



export function UserBlock({text, columns = 80, skipRows = 0, maxRows}: Props) {

  const t = tokens.transcript;

  const slice = sliceTextByWrappedRows(

    `❯ ${text}`,

    columns,

    skipRows,

    maxRows ?? Number.MAX_SAFE_INTEGER,

    USER_PREFIX_COLS,

  );

  return (
    <Box width="100%" marginBottom={1} paddingX={0}>
      <Text wrap="wrap">
        <Text bold color={t.userBorder}>
          │{' '}
        </Text>
        <Text bold color={t.user}>
          {slice.text}
        </Text>
      </Text>
    </Box>
  );

}

