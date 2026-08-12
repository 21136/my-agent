import React from 'react';
import {Box, Text} from 'ink';
import {parseMarkdown, type MarkdownBlock, type MarkdownInline} from '../markdown.js';
import {LineText} from '../components/LineText.js';
import {sliceTextByWrappedRows} from '../render/display-text.js';
import {tokens} from '../theme/tokens.js';

export function AssistantHeader({name}: {name: string}) {
  const t = tokens.transcript;
  return (
    <Box marginBottom={0}>
      <Text bold color={t.assistantHeader}>
        <Text color={t.assistantDiamond}>◆ </Text>{name}
      </Text>
    </Box>
  );
}

export function AssistantBody({children}: {children: React.ReactNode}) {
  return (
    <Box flexDirection="column" width="100%" marginBottom={1}>
      {children}
    </Box>
  );
}

export function Strong({children}: {children: React.ReactNode}) {
  return <Text bold color={tokens.transcript.assistantStrong}>{children}</Text>;
}

export function InlineCode({children}: {children: React.ReactNode}) {
  return <Text color={tokens.transcript.codeFg}>{children}</Text>;
}

export function PathText({children}: {children: React.ReactNode}) {
  return <Text bold underline color={tokens.transcript.path}>{children}</Text>;
}

function InlineMarkdown({inline}: {inline: MarkdownInline}) {
  const nested = 'children' in inline ? inline.children : undefined;
  const content = nested
    ? nested.map((child, index) => <InlineMarkdown key={index} inline={child} />)
    : inline.kind === 'break'
      ? '\n'
      : inline.text;
  switch (inline.kind) {
    case 'strong':
      return <Strong>{content}</Strong>;
    case 'em':
      return <Text italic>{content}</Text>;
    case 'code':
      return <InlineCode>{inline.text}</InlineCode>;
    case 'link':
      return <PathText>{content}</PathText>;
    case 'break':
      return <>{'\n'}</>;
    default:
      return <>{inline.text}</>;
  }
}

function MarkdownBlockView({block}: {block: MarkdownBlock}) {
  const t = tokens.transcript;
  switch (block.kind) {
    case 'heading':
      return (
        <Box marginTop={block.depth <= 2 ? 0 : 0} marginBottom={0}>
          <LineText
            text={block.text}
            color={block.depth <= 2 ? t.assistantStrong : t.assistantBody}
            bold={block.depth <= 2}
          />
        </Box>
      );
    case 'paragraph':
      return (
        <Box marginBottom={0}>
          <Text wrap="wrap" color={t.assistantBody}>
            {block.inlines.map((inline, index) => <InlineMarkdown key={index} inline={inline} />)}
          </Text>
        </Box>
      );
    case 'list':
      return (
        <Box flexDirection="column">
          {block.items.map((item, index) => (
            <Text key={index} wrap="wrap">
              {block.ordered ? `${index + 1}. ` : '• '}
              {item.map((inline, itemIndex) => <InlineMarkdown key={itemIndex} inline={inline} />)}
            </Text>
          ))}
        </Box>
      );
    case 'quote':
      return (
        <Box flexDirection="column" borderStyle="single" borderColor={t.turnSep} paddingX={1}>
          {block.blocks.map((child, index) => <MarkdownBlockView key={index} block={child} />)}
        </Box>
      );
    case 'code':
      return <LineText text={block.text} color={t.codeFg} />;
  }
}

type BodySliceProps = {
  text: string;
  columns: number;
  skipRows?: number;
  maxRows?: number;
};

/** Plain multiline body while markdown is still streaming / incomplete. */
export function PlainBody({text, columns, skipRows = 0, maxRows}: BodySliceProps) {
  const t = tokens.transcript;
  const slice = sliceTextByWrappedRows(
    text,
    columns,
    skipRows,
    maxRows ?? Number.MAX_SAFE_INTEGER,
  );
  if (!slice.text && skipRows > 0) return <AssistantBody>{null}</AssistantBody>;
  return (
    <AssistantBody>
      <LineText text={slice.text} color={t.assistantBody} />
    </AssistantBody>
  );
}

export function MarkdownBody({text, columns, skipRows = 0, maxRows}: BodySliceProps) {
  const t = tokens.transcript;
  const slice = sliceTextByWrappedRows(
    text,
    columns,
    skipRows,
    maxRows ?? Number.MAX_SAFE_INTEGER,
  );
  if (!slice.text && skipRows > 0) return <AssistantBody>{null}</AssistantBody>;
  return (
    <AssistantBody>
      {parseMarkdown(slice.text).map((block, index) => <MarkdownBlockView key={index} block={block} />)}
    </AssistantBody>
  );
}
