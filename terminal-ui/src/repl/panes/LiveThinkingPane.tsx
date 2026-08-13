import React, {memo} from 'react';
import {Box} from 'ink';
import {ThinkingBlock} from '../../blocks/ThinkingBlock.js';
import {MAX_THINKING_DISPLAY_LINES} from '../../perf/virtual-list.js';

type Props = {
  text: string;
  columns: number;
  active: boolean;
};

/** Live reasoning overlay — isolated from the static transcript viewport. */
export const LiveThinkingPane = memo(function LiveThinkingPane({
  text,
  columns,
  active,
}: Props) {
  if (!active) return null;

  return (
    <Box flexShrink={0} width="100%">
      <ThinkingBlock
        text={text}
        columns={columns}
        active
        collapsed={false}
        maxRows={MAX_THINKING_DISPLAY_LINES + 3}
      />
    </Box>
  );
});
