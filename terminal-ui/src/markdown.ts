import {marked, type Token, type Tokens} from 'marked';

export type MarkdownBlock =
  | {kind: 'heading'; depth: number; text: string}
  | {kind: 'paragraph'; inlines: MarkdownInline[]}
  | {kind: 'list'; ordered: boolean; items: MarkdownInline[][]}
  | {kind: 'quote'; blocks: MarkdownBlock[]}
  | {kind: 'code'; lang?: string; text: string};

export type MarkdownInline =
  | {kind: 'text'; text: string}
  | {kind: 'strong'; text: string; children: MarkdownInline[]}
  | {kind: 'em'; text: string; children: MarkdownInline[]}
  | {kind: 'code'; text: string}
  | {kind: 'link'; text: string; href: string; children: MarkdownInline[]}
  | {kind: 'break'};

const MARKDOWN_OPTIONS = {gfm: true, breaks: true};

function inlineTokens(tokens: Token[] = []): MarkdownInline[] {
  const result: MarkdownInline[] = [];
  for (const token of tokens) {
    switch (token.type) {
      case 'text':
      case 'escape':
        result.push({kind: 'text', text: token.text});
        break;
      case 'strong':
        result.push({kind: 'strong', text: token.text, children: inlineTokens(token.tokens)});
        break;
      case 'em':
        result.push({kind: 'em', text: token.text, children: inlineTokens(token.tokens)});
        break;
      case 'codespan':
        result.push({kind: 'code', text: token.text});
        break;
      case 'link':
        result.push({kind: 'link', text: token.text, href: token.href, children: inlineTokens(token.tokens)});
        break;
      case 'br':
        result.push({kind: 'break'});
        break;
      default:
        if ('text' in token && typeof token.text === 'string') {
          result.push({kind: 'text', text: token.text});
        }
    }
  }
  return result;
}

function blockTokens(tokens: Token[]): MarkdownBlock[] {
  const result: MarkdownBlock[] = [];
  for (const token of tokens) {
    switch (token.type) {
      case 'heading':
        result.push({kind: 'heading', depth: token.depth, text: token.text});
        break;
      case 'paragraph':
        result.push({kind: 'paragraph', inlines: inlineTokens(token.tokens ?? [])});
        break;
      case 'list':
        result.push({
          kind: 'list',
          ordered: token.ordered,
          items: token.items.map((item: Tokens.ListItem) => {
            const paragraph = item.tokens?.find((child) => child.type === 'paragraph');
            if (paragraph && paragraph.type === 'paragraph') {
              return inlineTokens(paragraph.tokens ?? []);
            }
            return inlineTokens(item.tokens ?? []);
          }),
        });
        break;
      case 'blockquote':
        result.push({kind: 'quote', blocks: blockTokens(token.tokens ?? [])});
        break;
      case 'code':
        result.push({kind: 'code', lang: token.lang || undefined, text: token.text});
        break;
      case 'space':
      case 'hr':
        break;
      default:
        if (token.type === 'html') {
          result.push({kind: 'paragraph', inlines: [{kind: 'text', text: token.text}]});
        }
    }
  }
  return result;
}

export function parseMarkdown(text: string): MarkdownBlock[] {
  if (!text.trim()) return [];
  const tokens = marked.lexer(text.trimEnd(), MARKDOWN_OPTIONS);
  return blockTokens(tokens);
}

export function markdownText(block: MarkdownBlock): string {
  if (block.kind === 'heading') return block.text;
  if (block.kind === 'code') return block.text;
  if (block.kind === 'quote') return block.blocks.map(markdownText).join('\n');
  if (block.kind === 'list') return block.items.map((item) => item.map((inline) => inline.kind === 'break' ? '\n' : inline.text).join('')).join('\n');
  return block.inlines.map((inline) => inline.kind === 'break' ? '\n' : inline.text).join('');
}

export type {Tokens};
