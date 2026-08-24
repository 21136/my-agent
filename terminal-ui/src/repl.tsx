import React, {useMemo} from 'react';
import type {TerminalBlock} from './types.js';
import type {TerminalChrome, TerminalLayoutProps, TerminalSession} from './repl/TerminalLayout.js';
import {TerminalLayout} from './repl/TerminalLayout.js';
import type {SlashCommand} from './slash-commands.js';
import type {TerminalResult} from './types.js';

export type ReplProps = {
  greet?: string;
  greetSub?: string;
  model?: string;
  root?: string;
  mascotLines?: string[];
  mascotLabel?: string;
  blocks?: TerminalBlock[];
  liveReasoningText?: string;
  liveAssistantText?: string;
  activeTool?: string;
  activeToolStartedAt?: number;
  planStatus?: string;
  working?: boolean;
  input?: string;
  confirm?: {requestId: string; preview: string; allowApproveAll: boolean};
  result?: TerminalResult;
  scrollUpRows?: number;
  newOutputRows?: number;
  slashCommands?: readonly SlashCommand[];
  slashCommandIndex?: number;
  height?: number;
  columns?: number;
};

export function Repl({
  greet = '下午好，忆梦。',
  greetSub = '',
  model = 'flash',
  root = 'D:/my-agent/workspace/huiyi',
  mascotLines = [],
  mascotLabel = '打工仔',
  blocks,
  liveReasoningText = '',
  liveAssistantText = '',
  activeTool,
  activeToolStartedAt,
  planStatus = '',
  working = true,
  input = '',
  confirm,
  result,
  scrollUpRows = 0,
  newOutputRows = 0,
  slashCommands = [],
  slashCommandIndex = 0,
  height,
  columns = 80,
}: ReplProps) {
  const session = useMemo(
    (): TerminalSession => ({
      greet,
      greetSub,
      model,
      root,
      mascotLines,
      mascotLabel,
    }),
    [greet, greetSub, model, root, mascotLines, mascotLabel],
  );
  const chrome = useMemo(
    (): TerminalChrome => ({
      working,
      activeTool,
      activeToolStartedAt,
      planStatus,
      confirm,
      result,
    }),
    [working, activeTool, activeToolStartedAt, planStatus, confirm, result],
  );

  const layoutProps: TerminalLayoutProps = {
    height,
    columns,
    session,
    chrome,
    ...(blocks !== undefined ? {blocks} : {}),
    liveReasoningText,
    liveAssistantText,
    input,
    scrollUpRows,
    newOutputRows,
    slashCommands,
    slashCommandIndex,
  };

  return <TerminalLayout {...layoutProps} />;
}

export {TerminalLayout};
export type {TerminalChrome, TerminalLayoutProps, TerminalSession};
