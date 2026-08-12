import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {dirname, join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {test} from 'node:test';
import {
  createEventReducer,
  createInitialState,
  parseTerminalJsonl,
  reduceEvents,
  reduceState,
  type TerminalEvent,
} from '../src/reduce/events.js';
import {parseMarkdown} from '../src/markdown.js';
import type {TerminalBlock} from '../src/types.js';
import {getVisibleBlocks} from '../src/perf/virtual-list.js';
import {throttle} from '../src/perf/throttle.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const fixturesDir = join(__dirname, '..', 'fixtures');

function loadFixture(name: string): TerminalEvent[] {
  const text = readFileSync(join(fixturesDir, name), 'utf8');
  return parseTerminalJsonl(text);
}

function blockKinds(blocks: TerminalBlock[]): string[] {
  return blocks.map((b) => b.kind);
}

test('IT-590b: demo-turn fixture reduces to expected block sequence', () => {
  const events = loadFixture('demo-turn.jsonl');
  const ui = reduceEvents(events);

  assert.equal(ui.greet, '下午好，忆梦。');
  assert.equal(ui.model, 'flash');
  assert.equal(ui.root, 'D:/my-agent/workspace/huiyi');
  assert.equal(ui.working, false);
  assert.equal(ui.activeTool, undefined);

  assert.deepEqual(blockKinds(ui.blocks), [
    'user',
    'thinking',
    'assistant',
    'user',
    'turn_sep',
    'thinking',
    'notice',
  ]);

  const firstUser = ui.blocks[0];
  assert.equal(firstUser.kind, 'user');
  if (firstUser.kind === 'user') {
    assert.match(firstUser.text, /DoctorController/);
  }

  const assistant = ui.blocks[2];
  assert.equal(assistant.kind, 'assistant');
  if (assistant.kind === 'assistant') {
    assert.equal(assistant.name, '打工仔');
    assert.match(assistant.body, /DoctorService\.list\(\)/);
    assert.match(assistant.body, /做了什么/);
  }

  const notice = ui.blocks[6];
  assert.equal(notice.kind, 'notice');
  if (notice.kind === 'notice') {
    assert.match(notice.text, /测试失败/);
  }
});

test('IT-590b: streaming fixture merges deltas', () => {
  const events = loadFixture('streaming.jsonl');
  const ui = reduceEvents(events);

  assert.deepEqual(blockKinds(ui.blocks), ['user', 'thinking', 'assistant']);
  const thinking = ui.blocks[1];
  assert.equal(thinking.kind, 'thinking');
  if (thinking.kind === 'thinking') {
    assert.equal(thinking.text, 'hm…');
  }
  const assistant = ui.blocks[2];
  assert.equal(assistant.kind, 'assistant');
  if (assistant.kind === 'assistant') {
    assert.equal(assistant.body, 'Hi there');
  }
});

test('IT-590b: tool events do not appear in transcript blocks', () => {
  const events: TerminalEvent[] = [
    {type: 'user.message', text: 'run'},
    {type: 'turn.start'},
    {type: 'tool.active', name: 'run_command'},
    {type: 'tool.clear'},
  ];
  const ui = reduceEvents(events);
  assert.deepEqual(blockKinds(ui.blocks), ['user', 'thinking']);
  assert.equal(ui.activeTool, undefined);
  assert.equal(ui.activeToolStartedAt, undefined);
});

test('IT-595: tool.active records started_at for status bar elapsed', () => {
  const ui = reduceEvents([
    {type: 'tool.active', name: 'grep', started_at: 1_700_000_000_000},
  ]);
  assert.equal(ui.activeTool, 'grep');
  assert.equal(ui.activeToolStartedAt, 1_700_000_000_000);

  const cleared = reduceEvents(
    [
      {type: 'tool.active', name: 'grep'},
      {type: 'tool.clear'},
    ],
    ui,
  );
  assert.equal(cleared.activeTool, undefined);
  assert.equal(cleared.activeToolStartedAt, undefined);
});

test('IT-590b: turn.start inserts separator after first turn', () => {
  let turnCount = 0;
  let state = createInitialState();

  ({state, turnCount} = reduceState(state, {type: 'turn.start'}, turnCount));
  assert.deepEqual(blockKinds(state.blocks), ['thinking']);
  assert.equal(turnCount, 1);

  state = {
    ...state,
    blocks: [...state.blocks, {kind: 'user', text: 'a'}],
  };

  ({state, turnCount} = reduceState(state, {type: 'turn.start'}, turnCount));
  assert.deepEqual(blockKinds(state.blocks), ['thinking', 'user', 'turn_sep', 'thinking']);
  assert.equal(turnCount, 2);
});

test('IT-590b: createEventReducer reduce() returns blocks snapshot', () => {
  const reducer = createEventReducer();
  assert.deepEqual(reducer.reduce({type: 'user.message', text: 'ping'}), [
    {kind: 'user', text: 'ping'},
  ]);
  assert.deepEqual(reducer.getState().blocks, [{kind: 'user', text: 'ping'}]);
});

test('IT-590b: malformed JSONL lines are skipped', () => {
  const events = parseTerminalJsonl('not json\n{"type":"notice","text":"ok"}\n');
  assert.equal(events.length, 1);
  assert.equal(events[0]?.type, 'notice');
});

test('IT-592b: confirm state and clear preserve session data', () => {
  const ui = reduceEvents([
    {type: 'session.init', greet: '真实问候', model: 'pro', root: 'D:/workspace'},
    {type: 'user.message', text: 'write'},
    {type: 'tool.active', name: 'write_text'},
    {type: 'confirm.request', request_id: 'r1', preview: 'write file', allow_approve_all: true},
    {type: 'confirm.done', request_id: 'r1', choice: 'y'},
    {type: 'transcript.clear'},
  ]);
  assert.equal(ui.greet, '真实问候');
  assert.equal(ui.model, 'pro');
  assert.equal(ui.root, 'D:/workspace');
  assert.deepEqual(ui.blocks, []);
  assert.equal(ui.activeTool, undefined);
  assert.equal(ui.confirm, undefined);
});

test('IT-593: turn.start opens thinking placeholder until assistant streams', () => {
  const reducer = createEventReducer();
  reducer.reduce({type: 'user.message', text: 'hi'});
  reducer.reduce({type: 'turn.start'});
  assert.deepEqual(reducer.getState().blocks, [
    {kind: 'user', text: 'hi'},
    {kind: 'thinking', text: ''},
  ]);

  reducer.reduce({type: 'reasoning.delta', text: 'trace'});
  assert.deepEqual(reducer.getState().blocks[1], {kind: 'thinking', text: 'trace'});

  reducer.reduce({type: 'assistant.delta', text: 'answer'});
  assert.deepEqual(reducer.getState().blocks.map((block) => block.kind), [
    'user',
    'thinking',
    'assistant_streaming',
  ]);
});

test('IT-593: assistant markdown deltas finalize from the chat-state buffer', () => {
  const reducer = createEventReducer();
  reducer.reduce({type: 'turn.start'});
  reducer.reduce({type: 'assistant.delta', text: '# Title\n\n**bold** '});
  reducer.reduce({type: 'assistant.delta', text: 'and `code`'});
  assert.deepEqual(reducer.getState().blocks.map((block) => block.kind), ['assistant_streaming']);
  assert.equal(reducer.getState().assistantBuffer, '# Title\n\n**bold** and `code`');

  reducer.reduce({type: 'assistant.done', text: ''});
  assert.deepEqual(reducer.getState().blocks, [
    {kind: 'assistant', name: '打工仔', body: '# Title\n\n**bold** and `code`', turnIndex: 1},
  ]);
  assert.equal(reducer.getState().assistantBuffer, '');
});

test('IT-593: assistant.done is idempotent and explicit text wins', () => {
  const reducer = createEventReducer();
  reducer.reduce({type: 'turn.start'});
  reducer.reduce({type: 'assistant.delta', text: 'draft'});
  reducer.reduce({type: 'assistant.done', text: 'final'});
  reducer.reduce({type: 'assistant.done', text: 'final'});
  assert.deepEqual(reducer.getState().blocks, [
    {kind: 'assistant', name: '打工仔', body: 'final', turnIndex: 1},
  ]);
});

test('IT-593: markdown subset exposes structured Ink-safe blocks', () => {
  const blocks = parseMarkdown('# Title\n\n**bold** and `code`\n\n- one\n- two\n\n> quoted\n\n```ts\nconst x = 1;\n```');
  assert.equal(blocks[0]?.kind, 'heading');
  assert.equal(blocks[1]?.kind, 'paragraph');
  assert.equal(blocks[2]?.kind, 'list');
  assert.equal(blocks[3]?.kind, 'quote');
  assert.equal(blocks[4]?.kind, 'code');
  const nested = parseMarkdown('**bold `code`**')[0];
  assert.equal(nested?.kind, 'paragraph');
  if (nested?.kind === 'paragraph') {
    assert.equal(nested.inlines[0]?.kind, 'strong');
    assert.equal(nested.inlines[0]?.children[1]?.kind, 'code');
  }
});

test('IT-593: tools and confirms stay outside markdown transcript blocks', () => {
  const ui = reduceEvents([
    {type: 'turn.start'},
    {type: 'assistant.delta', text: 'answer'},
    {type: 'tool.active', name: 'write_text'},
    {type: 'confirm.request', request_id: 'r3', preview: 'write', allow_approve_all: false},
    {type: 'confirm.done', request_id: 'r3', choice: 'y'},
    {type: 'tool.clear'},
    {type: 'assistant.done', text: ''},
  ]);
  assert.deepEqual(blockKinds(ui.blocks), ['assistant']);
  assert.equal(ui.activeTool, undefined);
  assert.equal(ui.confirm, undefined);
});

test('IT-594: transcript updates throttle to one leading and trailing publish', async () => {
  const calls: number[] = [];
  const publish = throttle((value: number) => calls.push(value), 20);
  publish(1);
  publish(2);
  assert.deepEqual(calls, [1]);
  await new Promise((resolve) => setTimeout(resolve, 25));
  assert.deepEqual(calls, [1, 2]);
  publish.cancel();
});

test('IT-594: virtual transcript keeps the newest bounded window', () => {
  const blocks: TerminalBlock[] = Array.from({length: 205}, (_, index) => ({kind: 'notice', text: String(index)}));
  const visible = getVisibleBlocks(blocks, 200);
  assert.equal(visible.length, 200);
  assert.equal(visible[0]?.kind, 'notice');
  if (visible[0]?.kind === 'notice') assert.equal(visible[0].text, '5');
  assert.equal(getVisibleBlocks(blocks, 300), blocks);
});

test('IT-593: clear resets markdown chat state and turns do not merge', () => {
  const reducer = createEventReducer();
  reducer.reduce({type: 'turn.start'});
  reducer.reduce({type: 'assistant.delta', text: 'first'});
  reducer.reduce({type: 'assistant.done', text: ''});
  reducer.reduce({type: 'turn.start'});
  reducer.reduce({type: 'assistant.delta', text: 'second'});
  assert.deepEqual(blockKinds(reducer.getState().blocks), ['assistant', 'turn_sep', 'assistant_streaming']);
  assert.equal(reducer.getState().turnIndex, 2);

  reducer.reduce({type: 'transcript.clear'});
  assert.deepEqual(reducer.getState().blocks, []);
  assert.equal(reducer.getState().turnIndex, 0);
  assert.equal(reducer.getState().assistantBuffer, '');
});
