import React, {memo} from 'react';
import {Box, Text} from 'ink';
import {tokens} from '../../theme/tokens.js';
import type {SlashCommand} from '../../slash-commands.js';
import type {ModelOption} from '../../model-picker.js';
import {MODEL_PICKER_MAX_VISIBLE, modelPickerVisibleWindow} from '../../model-picker.js';

type Props = {
  input: string;
  confirm?: {requestId: string; preview: string; allowApproveAll: boolean};
  working?: boolean;
  scrollUpRows?: number;
  newOutputRows?: number;
  slashCommands?: readonly SlashCommand[];
  slashCommandIndex?: number;
  modelPickerOpen?: boolean;
  modelOptions?: readonly ModelOption[];
  modelPickerIndex?: number;
  currentModel?: string;
};

export const ComposerPane = memo(function ComposerPane({
  input,
  confirm,
  working,
  scrollUpRows = 0,
  newOutputRows = 0,
  slashCommands = [],
  slashCommandIndex = 0,
  modelPickerOpen = false,
  modelOptions = [],
  modelPickerIndex = 0,
  currentModel = '',
}: Props) {
  const pickerWindow = modelPickerVisibleWindow(
    modelOptions.length,
    modelPickerIndex,
    MODEL_PICKER_MAX_VISIBLE,
  );
  const visibleModels = modelOptions.slice(pickerWindow.start, pickerWindow.end);
  const hiddenAbove = pickerWindow.start;
  const hiddenBelow = modelOptions.length - pickerWindow.end;

  return (
    <Box
      width="100%"
      flexDirection="column"
      borderStyle="round"
      borderColor={tokens.transcript.turnSep}
      paddingX={1}
      flexShrink={0}
    >
      {modelPickerOpen ? (
        <Box flexDirection="column" paddingLeft={1}>
          <Text color={tokens.status.muted}>
            选择模型{currentModel ? ` · 当前 ${currentModel}` : ''} · ↑↓ 移动 · Enter 确认 · Esc 取消
          </Text>
          {hiddenAbove > 0 ? (
            <Text color={tokens.status.muted}>  ↑ 还有 {hiddenAbove} 项</Text>
          ) : null}
          {visibleModels.map((entry, offset) => {
            const index = pickerWindow.start + offset;
            return (
            <Text
              key={entry.id}
              color={index === modelPickerIndex ? tokens.prompt : tokens.status.muted}
            >
              {index === modelPickerIndex ? '❯ ' : '  '}
              {entry.name} ({entry.id}
              {entry.tier ? ` · ${entry.tier.toUpperCase()}` : ''})
              {entry.id === currentModel ? ' ← 当前' : ''}
            </Text>
            );
          })}
          {hiddenBelow > 0 ? (
            <Text color={tokens.status.muted}>  ↓ 还有 {hiddenBelow} 项</Text>
          ) : null}
        </Box>
      ) : null}
      {!confirm && !modelPickerOpen && slashCommands.length > 0 ? (
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
