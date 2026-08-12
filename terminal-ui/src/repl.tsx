import React, {useMemo} from 'react';
import {Box, Text} from 'ink';
import {UserBlock} from './blocks/UserBlock.js';
import {ThinkingBlock} from './blocks/ThinkingBlock.js';
import {
  AssistantBody,
  AssistantHeader,
  MarkdownBody,
} from './blocks/AssistantBlock.js';
import {NoticeBlock} from './blocks/NoticeBlock.js';
import {WelcomePanel} from './components/WelcomePanel.js';
import {WelcomeCompact} from './components/WelcomeCompact.js';
import {StatusBar} from './components/StatusBar.js';
import {TurnSep} from './components/TurnSep.js';
import {tokens} from './theme/tokens.js';
import type {TerminalBlock} from './types.js';
import {
  getViewportBlockEntries,
  transcriptRowBudget,
  ASSISTANT_HEADER_ROWS,
} from './perf/virtual-list.js';

type ComposerProps = {
  input: string;
  confirm?: {requestId: string; preview: string; allowApproveAll: boolean};
};

function Composer({input, confirm}: ComposerProps) {
  return (
    <Box
      width="100%"
      flexDirection="column"
      borderStyle="round"
      borderColor={tokens.transcript.turnSep}
      paddingX={1}
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
}

type Props = {
  greet?: string;
  greetSub?: string;
  model?: string;
  root?: string;
  mascotLines?: string[];
  mascotLabel?: string;
  blocks?: TerminalBlock[];
  activeTool?: string;
  activeToolStartedAt?: number;
  working?: boolean;
  input?: string;
  confirm?: {requestId: string; preview: string; allowApproveAll: boolean};
  /** Rows scrolled up from the bottom of the transcript (0 = follow latest). */
  scrollUpRows?: number;
  /** Viewport rows — pins composer + status bar to the bottom (Claude-style). */
  height?: number;
  columns?: number;
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

type BlockViewProps = {
  block: TerminalBlock;
  columns: number;
  thinkingActive: boolean;
  skipRows: number;
  maxRows: number;
};

function BlockView({block, columns, thinkingActive, skipRows, maxRows}: BlockViewProps) {
  switch (block.kind) {
    case 'user':
      return (
        <UserBlock
          text={block.text}
          columns={columns}
          skipRows={skipRows}
          maxRows={maxRows}
        />
      );
    case 'thinking':
      return (
        <ThinkingBlock
          text={block.text}
          columns={columns}
          active={thinkingActive}
          skipRows={skipRows}
          maxRows={maxRows}
        />
      );
    case 'assistant': {
      const showHeader = skipRows < ASSISTANT_HEADER_ROWS;
      const bodySkip = Math.max(0, skipRows - ASSISTANT_HEADER_ROWS);
      const bodyMax = Math.max(
        0,
        maxRows - (showHeader ? ASSISTANT_HEADER_ROWS - skipRows : 0),
      );
      return (
        <>
          {showHeader ? (
            <AssistantHeader name={block.name} />
          ) : null}
          {block.body ? (
            <MarkdownBody
              text={block.body}
              columns={columns}
              skipRows={bodySkip}
              maxRows={bodyMax > 0 ? bodyMax : Number.MAX_SAFE_INTEGER}
            />
          ) : (
            <AssistantBody>{null}</AssistantBody>
          )}
        </>
      );
    }
    case 'assistant_streaming': {
      const showHeader = skipRows < ASSISTANT_HEADER_ROWS;
      const bodySkip = Math.max(0, skipRows - ASSISTANT_HEADER_ROWS);
      const bodyMax = Math.max(
        0,
        maxRows - (showHeader ? ASSISTANT_HEADER_ROWS - skipRows : 0),
      );
      return (
        <>
          {showHeader ? (
            <AssistantHeader name={block.name} />
          ) : null}
          {block.body ? (
            <MarkdownBody
              text={block.body}
              columns={columns}
              skipRows={bodySkip}
              maxRows={bodyMax > 0 ? bodyMax : Number.MAX_SAFE_INTEGER}
            />
          ) : (
            <AssistantBody>{null}</AssistantBody>
          )}
        </>
      );
    }
    case 'notice':
      return <NoticeBlock text={block.text} />;
    case 'turn_sep':
      return <TurnSep />;
    default:
      return null;
  }
}

export function Repl({
  greet = '下午好，忆梦。',
  greetSub = '',
  model = 'flash',
  root = 'D:/my-agent/workspace/huiyi',
  mascotLines = [],
  mascotLabel = '打工仔',
  blocks = DEMO_BLOCKS,
  activeTool = 'run_command',
  activeToolStartedAt,
  working = true,
  input = '',
  confirm,
  scrollUpRows = 0,
  height,
  columns = 80,
}: Props) {
  const welcomeCompact = blocks.length > 0;
  const transcriptRows = transcriptRowBudget(height, welcomeCompact);
  const {entries: visibleBlocks, clippedTop, clippedBottom} = useMemo(
    () => getViewportBlockEntries(blocks, transcriptRows, columns, scrollUpRows),
    [blocks, transcriptRows, columns, scrollUpRows],
  );
  const lastIndex = visibleBlocks.at(-1)?.index;

  return (
    <Box
      flexDirection="column"
      paddingX={1}
      paddingY={0}
      height={height}
      width="100%"
    >
      <Box flexShrink={0} width="100%">
        {welcomeCompact ? (
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
      <Box
        flexDirection="column"
        flexGrow={1}
        minHeight={0}
        overflow="hidden"
        width="100%"
      >
        {clippedTop ? (
          <Text color={tokens.status.muted}>较早消息在上方（滚轮 / PgUp / ↑）</Text>
        ) : null}
        {visibleBlocks.map(({block, index, skipRows, maxRows}) => (
          <Box key={`${block.kind}-${index}`} flexShrink={0} width="100%">
            <BlockView
              block={block}
              columns={columns}
              skipRows={skipRows}
              maxRows={maxRows}
              thinkingActive={
                working && block.kind === 'thinking' && index === lastIndex
              }
            />
          </Box>
        ))}
        {clippedBottom ? (
          <Text color={tokens.status.muted}>较新消息在下方（滚轮 / PgDn / ↓）</Text>
        ) : null}
        {scrollUpRows === 0 ? <Box flexGrow={1} /> : null}
      </Box>
      <Box flexDirection="column" flexShrink={0} width="100%">
        <Composer input={input} confirm={confirm} />
        <StatusBar
          model={model}
          root={root}
          working={working}
          toolName={activeTool}
          toolStartedAt={activeToolStartedAt}
        />
      </Box>
    </Box>
  );
}
