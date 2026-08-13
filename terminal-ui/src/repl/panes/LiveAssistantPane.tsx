import React, {memo} from 'react';
import {Box} from 'ink';
import {AssistantHeader, MarkdownBody} from '../../blocks/AssistantBlock.js';
import {DEFAULT_ASSISTANT_NAME} from '../../reduce/events.js';

type Props = {
  body: string;
  columns: number;
  active: boolean;
  name?: string;
};

/** Live assistant reply overlay — isolated from the static transcript viewport. */
export const LiveAssistantPane = memo(function LiveAssistantPane({
  body,
  columns,
  active,
  name = DEFAULT_ASSISTANT_NAME,
}: Props) {
  if (!active || !body) return null;

  return (
    <Box flexShrink={0} width="100%" flexDirection="column">
      <AssistantHeader name={name} />
      <MarkdownBody
        text={body}
        columns={columns}
        skipRows={0}
        maxRows={Number.MAX_SAFE_INTEGER}
      />
    </Box>
  );
});
