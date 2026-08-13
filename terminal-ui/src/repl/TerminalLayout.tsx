import React, {useEffect, useMemo, useState} from 'react';
import {Box} from 'ink';
import type {TerminalBlock} from '../types.js';
import {transcriptRowBudget} from '../perf/virtual-list.js';
import {
  committedTranscriptBlocks,
  filterExpiredNotices,
  trailingAssistantStreaming,
  trailingThinkingActive,
} from './committed-blocks.js';
import {WelcomePane} from './panes/WelcomePane.js';
import {TranscriptPane} from './panes/TranscriptPane.js';
import {LiveThinkingPane} from './panes/LiveThinkingPane.js';
import {LiveAssistantPane} from './panes/LiveAssistantPane.js';
import {ComposerPane} from './panes/ComposerPane.js';
import {StatusPane} from './panes/StatusPane.js';

export type TerminalChrome = {
  working: boolean;
  activeTool?: string;
  activeToolStartedAt?: number;
  planStatus?: string;
  confirm?: {requestId: string; preview: string; allowApproveAll: boolean};
};

export type TerminalSession = {
  greet: string;
  greetSub: string;
  model: string;
  root: string;
  mascotLines: string[];
  mascotLabel: string;
};

export type TerminalLayoutProps = {
  height?: number;
  columns?: number;
  session: TerminalSession;
  chrome: TerminalChrome;
  blocks: readonly TerminalBlock[];
  liveReasoningText?: string;
  liveAssistantText?: string;
  input?: string;
  scrollUpRows?: number;
};

const DEMO_BLOCKS: TerminalBlock[] = [
  {
    kind: 'user',
    text: '帮我看看 DoctorController 的列表接口为什么 500',
  },
  {
    kind: 'thinking',
    text: '需要先定位 Controller 和 Service，再跑测试复现…',
  },
  {
    kind: 'assistant',
    name: '打工仔',
    body: '',
  },
  {kind: 'turn_sep'},
  {kind: 'user', text: '跑一下测试确认'},
  {kind: 'thinking', text: '重跑 DoctorControllerTest…'},
  {kind: 'notice', text: '⚠ 测试失败：exit 1 — 见上文修复是否已保存'},
];

const DEFAULT_SESSION: TerminalSession = {
  greet: '下午好，忆梦。',
  greetSub: '',
  model: 'flash',
  root: 'D:/my-agent/workspace/huiyi',
  mascotLines: [],
  mascotLabel: '打工仔',
};

const DEFAULT_CHROME: TerminalChrome = {
  working: true,
  activeTool: 'run_command',
  planStatus: '',
};

export function TerminalLayout({
  height,
  columns = 80,
  session = DEFAULT_SESSION,
  chrome = DEFAULT_CHROME,
  blocks = DEMO_BLOCKS,
  liveReasoningText = '',
  liveAssistantText = '',
  input = '',
  scrollUpRows = 0,
}: TerminalLayoutProps) {
  const [now, setNow] = useState(() => Date.now());
  const visibleBlocks = useMemo(
    () => filterExpiredNotices(blocks, now),
    [blocks, now],
  );
  const hasEphemeralNotices = useMemo(
    () => visibleBlocks.some((block) => block.kind === 'notice' && block.ephemeral),
    [visibleBlocks],
  );

  useEffect(() => {
    if (!hasEphemeralNotices) return;
    const timer = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(timer);
  }, [hasEphemeralNotices]);

  const welcomeCompact = visibleBlocks.length > 0;
  const transcriptRows = transcriptRowBudget(height, welcomeCompact);
  const assistantLive = trailingAssistantStreaming(
    visibleBlocks,
    chrome.working,
    liveAssistantText,
  );
  const thinkingLive =
    trailingThinkingActive(
      visibleBlocks,
      chrome.working,
      liveReasoningText,
      chrome.planStatus ?? '',
    ) && !assistantLive;

  const staticBlocks = useMemo(
    () =>
      committedTranscriptBlocks(visibleBlocks, {
        stripActiveThinking: thinkingLive,
        stripStreamingAssistant: assistantLive,
      }),
    [assistantLive, thinkingLive, visibleBlocks],
  );

  return (
    <Box
      flexDirection="column"
      paddingX={1}
      paddingY={0}
      height={height}
      width="100%"
    >
      <WelcomePane compact={welcomeCompact} columns={columns} {...session} />
      <Box
        flexDirection="column"
        flexGrow={1}
        minHeight={0}
        overflow="hidden"
        width="100%"
      >
        <TranscriptPane
          blocks={staticBlocks}
          columns={columns}
          transcriptRows={transcriptRows}
          scrollUpRows={scrollUpRows}
        />
        <LiveThinkingPane
          text={liveReasoningText}
          columns={columns}
          active={thinkingLive}
        />
        <LiveAssistantPane
          body={liveAssistantText}
          columns={columns}
          active={assistantLive}
        />
        {scrollUpRows === 0 ? <Box flexGrow={1} /> : null}
      </Box>
      <ComposerPane input={input} confirm={chrome.confirm} />
      <StatusPane
        model={session.model}
        root={session.root}
        working={chrome.working}
        activeTool={chrome.activeTool}
        activeToolStartedAt={chrome.activeToolStartedAt}
        planStatus={chrome.planStatus}
      />
    </Box>
  );
}
