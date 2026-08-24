import React, {memo} from 'react';
import {Box, Text} from 'ink';
import {tokens} from '../../theme/tokens.js';
import type {SlashCommand} from '../../slash-commands.js';

type Props = {
  input: string;
  confirm?: {requestId: string; preview: string; allowApproveAll: boolean};
  working?: boolean;
  scrollUpRows?: number;
  newOutputRows?: number;
  slashCommands?: readonly SlashCommand[];
  slashCommandIndex?: number;
};

export const ComposerPane = memo(function ComposerPane({
  input,
  confirm,
  working,
  scrollUpRows = 0,
  newOutputRows = 0,
  slashCommands = [],
  slashCommandIndex = 0,
}: Props) {
  return (
    <Box
      width="100%"
      flexDirection="column"
      borderStyle="round"
      borderColor={tokens.transcript.turnSep}
      paddingX={1}
      flexShrink={0}
    >
      {!confirm && slashCommands.length > 0 ? (
        <Box flexDirection="column" paddingLeft={1}>
          {slashCommands.map((command, index) => (
            <Text key={command.name} color={index === slashCommandIndex ? tokens.prompt : tokens.status.muted}>
              {index === slashCommandIndex ? '❯ ' : '  '}{command.name} — {command.description}
              {command.suffix ? ` (${command.suffix})` : ''}
            </Text>
          ))}
        </Box>
      ) : null}
      {confirm ? (
        <>
          <Text color={tokens.transcript.noticeWarn}>Confirm: {confirm.preview}</Text>
          <Text color={tokens.prompt}>
            [y]es / [n]o{confirm.allowApproveAll ? ' / [a]ll' : ''}
          </Text>
        </>
      ) : (
        <>
          <Text color={tokens.prompt}>
            <Text bold>&gt; </Text>{input}<Text inverse> </Text>
          </Text>
          {working ? <Text color={tokens.status.working}>◐ Agent 正在运行 · Esc/Ctrl+C 取消</Text> : null}
          {scrollUpRows > 0 ? (
            <Text color={tokens.status.muted}>
              {newOutputRows > 0
                ? `↓ ${newOutputRows} 行新内容 · End 回到底部`
                : '↓ 已暂停跟随 · End 回到底部'}
            </Text>
          ) : null}
        </>
      )}
    </Box>
  );
});
