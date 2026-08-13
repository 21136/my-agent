import React, {memo} from 'react';
import {Box, Text} from 'ink';
import {tokens} from '../../theme/tokens.js';

type Props = {
  input: string;
  confirm?: {requestId: string; preview: string; allowApproveAll: boolean};
};

export const ComposerPane = memo(function ComposerPane({input, confirm}: Props) {
  return (
    <Box
      width="100%"
      flexDirection="column"
      borderStyle="round"
      borderColor={tokens.transcript.turnSep}
      paddingX={1}
      flexShrink={0}
    >
      {confirm ? (
        <>
          <Text color={tokens.transcript.noticeWarn}>Confirm: {confirm.preview}</Text>
          <Text color={tokens.prompt}>
            [y]es / [n]o{confirm.allowApproveAll ? ' / [a]ll' : ''}
          </Text>
        </>
      ) : (
        <Text color={tokens.prompt}>
          <Text bold>&gt; </Text>{input}<Text inverse> </Text>
        </Text>
      )}
    </Box>
  );
});
