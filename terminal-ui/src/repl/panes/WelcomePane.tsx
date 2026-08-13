import React, {memo} from 'react';
import {Box} from 'ink';
import {WelcomePanel} from '../../components/WelcomePanel.js';
import {WelcomeCompact} from '../../components/WelcomeCompact.js';

type Props = {
  compact: boolean;
  greet: string;
  greetSub: string;
  model: string;
  root: string;
  mascotLines: string[];
  mascotLabel: string;
  columns: number;
};

export const WelcomePane = memo(function WelcomePane({
  compact,
  greet,
  greetSub,
  model,
  root,
  mascotLines,
  mascotLabel,
  columns,
}: Props) {
  return (
    <Box flexShrink={0} width="100%">
      {compact ? (
        <WelcomeCompact greet={greet} model={model} root={root} columns={columns} />
      ) : (
        <WelcomePanel
          greet={greet}
          greetSub={greetSub}
          model={model}
          root={root}
          mascotLines={mascotLines}
          mascotLabel={mascotLabel}
        />
      )}
    </Box>
  );
});
