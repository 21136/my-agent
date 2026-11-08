import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';

const ts = createRequire(import.meta.url)('typescript');
const cache = new Map();
function load(url) {
  if (cache.has(url.href)) return cache.get(url.href);
  const out = {};
  cache.set(url.href, out);
  const js = ts.transpileModule(readFileSync(url, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  new Function('exports', 'require', js)(out, id => load(new URL(id.endsWith('.ts') ? id : `${id}.ts`, url)));
  return out;
}

const { createChatSession } = load(new URL('../src/shells/chat-state.ts', import.meta.url));

test('legacy session.banner topic metadata is accepted but not displayed', () => {
  const chat = createChatSession({ showProcess: true, confirmInBlocks: false });
  assert.equal(chat.handleEvent({
    type: 'session.banner', session_id: 'legacy-session', topics: ['coding'],
    turn_mode: 'agent', turn_mode_label: '', phase: 'S4',
  }), true);
  assert.equal(chat.model.sessionId, 'legacy-session');
  assert.equal(JSON.stringify(chat.model.blocks), '[]');
});
