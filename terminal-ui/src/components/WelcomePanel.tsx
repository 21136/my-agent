import React from 'react';
import {Box, Text} from 'ink';
import {AnsiSpriteLine} from './AnsiSpriteLine.js';
import {tokens} from '../theme/tokens.js';

const SPRITE_WIDTH = 32;
const SPRITE_COL = SPRITE_WIDTH + 2;
const UI_VERSION = '0.3.2';

type Props = {
  greet: string;
  greetSub?: string;
  model: string;
  root: string;
  mascotLines?: string[];
  mascotLabel?: string;
};

function MetaRow({model, root}: {model: string; root: string}) {
  const w = tokens.welcome;
  return (
    <Text>
      <Text bold color={w.metaModel}>
        {model}
      </Text>
      <Text color={w.metaSep}> · </Text>
      <Text color={w.metaRoot}>{root}</Text>
    </Text>
  );
}

function LeftCell({
  rowIndex,
  greet,
  greetSub,
  model,
  root,
}: {
  rowIndex: number;
  greet: string;
  greetSub: string;
  model: string;
  root: string;
}) {
  const w = tokens.welcome;
  if (rowIndex === 0) {
    return (
      <Text bold color={w.greet}>
        {`  ${greet}`}
      </Text>
    );
  }
  if (rowIndex === 1 && greetSub) {
    return <Text color={w.sub}>{`  ${greetSub}`}</Text>;
  }
  if (rowIndex === 2) {
    return <MetaRow model={model} root={root} />;
  }
  return <Text> </Text>;
}

export function WelcomePanel({
  greet,
  greetSub = '',
  model,
  root,
  mascotLines = [],
  mascotLabel = '打工仔',
}: Props) {
  const w = tokens.welcome;
  const hasMascot = mascotLines.length > 0;
  const titleLeft = ' my-agent ';
  const titleRight = ` v${UI_VERSION} `;

  return (
    <Box
      width="100%"
      marginBottom={1}
      flexDirection="column"
      borderStyle="round"
      borderColor={w.border}
      paddingX={0}
      paddingY={0}
    >
      <Box paddingX={1} width="100%">
        <Text color={w.border}>
          <Text color={w.border}>{titleLeft}</Text>
          <Text color={w.border}>{'─'.repeat(8)}</Text>
          <Text color={w.border}>{titleRight}</Text>
        </Text>
      </Box>
      {hasMascot ? (
        <>
          {mascotLines.map((spriteLine, rowIndex) => (
            <Box key={`welcome-row-${rowIndex}`} width="100%" flexDirection="row" paddingX={1}>
              <Box flexGrow={1} minWidth={28}>
                <LeftCell
                  rowIndex={rowIndex}
                  greet={greet}
                  greetSub={greetSub}
                  model={model}
                  root={root}
                />
              </Box>
              <Box width={SPRITE_COL} justifyContent="flex-end">
                <AnsiSpriteLine line={spriteLine} />
              </Box>
            </Box>
          ))}
          <Box width="100%" flexDirection="row" paddingX={1} marginBottom={0}>
            <Box flexGrow={1} />
            <Box width={SPRITE_COL} justifyContent="center">
              <Text color={w.sub}>{mascotLabel}</Text>
            </Box>
          </Box>
        </>
      ) : (
        <>
          <Box paddingX={1}>
            <Text bold color={w.greet}>
              {`  ${greet}`}
            </Text>
          </Box>
          {greetSub ? (
            <Box paddingX={1}>
              <Text color={w.sub}>{`  ${greetSub}`}</Text>
            </Box>
          ) : null}
          <Box paddingX={1}>
            <MetaRow model={model} root={root} />
          </Box>
        </>
      )}
    </Box>
  );
}
