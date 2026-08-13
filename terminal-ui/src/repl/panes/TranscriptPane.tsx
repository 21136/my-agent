import React, {memo, useMemo} from 'react';
import {Box} from 'ink';
import {UserBlock} from '../../blocks/UserBlock.js';
import {ThinkingBlock} from '../../blocks/ThinkingBlock.js';
import {
  AssistantBody,
  AssistantHeader,
  MarkdownBody,
} from '../../blocks/AssistantBlock.js';
import {NoticeBlock} from '../../blocks/NoticeBlock.js';
import {TurnSep} from '../../components/TurnSep.js';
import type {TerminalBlock} from '../../types.js';
import {
  getViewportBlockEntries,
  ASSISTANT_HEADER_ROWS,
} from '../../perf/virtual-list.js';

type BlockViewProps = {
  block: TerminalBlock;
  columns: number;
  skipRows: number;
  maxRows: number;
};

const StaticBlockView = memo(function StaticBlockView({
  block,
  columns,
  skipRows,
  maxRows,
}: BlockViewProps) {
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
          active={false}
          collapsed={Boolean(block.collapsed)}
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
          {showHeader ? <AssistantHeader name={block.name} /> : null}
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
});

type Props = {
  blocks: readonly TerminalBlock[];
  columns: number;
  transcriptRows: number;
  scrollUpRows: number;
};

export const TranscriptPane = memo(function TranscriptPane({
  blocks,
  columns,
  transcriptRows,
  scrollUpRows,
}: Props) {
  const {entries: visibleBlocks} = useMemo(
    () => getViewportBlockEntries(blocks, transcriptRows, columns, scrollUpRows),
    [blocks, transcriptRows, columns, scrollUpRows],
  );

  return (
    <>
      {visibleBlocks.map(({block, index, skipRows, maxRows}) => (
        <Box key={`${block.kind}-${index}`} flexShrink={0} width="100%">
          <StaticBlockView
            block={block}
            columns={columns}
            skipRows={skipRows}
            maxRows={maxRows}
          />
        </Box>
      ))}
    </>
  );
});
