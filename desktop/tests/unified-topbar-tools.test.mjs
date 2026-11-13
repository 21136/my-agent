import assert from 'node:assert/strict';
import test from 'node:test';
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

const { renderTopbarV2 } = load(new URL('../src/shells/unified/topbar.ts', import.meta.url));

function makeContainer() {
  const listeners = new Map();
  return {
    innerHTML: '',
    classList: { add() {}, remove() {} },
    querySelector(selector) {
      return {
        addEventListener(type, listener) {
          listeners.set(`${selector}:${type}`, listener);
        },
      };
    },
    querySelectorAll() { return []; },
    click(id) {
      const listener = listeners.get(`#${id}:click`);
      assert.ok(listener, `expected a click handler for ${id}`);
      listener();
    },
  };
}

const baseState = {
  proposals: [],
  intentLabel: '',
  checkerLabel: '',
  memoryLabel: '记忆 8 · 待审 2',
  memoryCount: 8,
  pendingMemoryCount: 2,
  projectLabel: '示例项目',
  contextLabel: '',
  sessionCount: 3,
  headerCtas: [],
};

test('unified topbar hides memory entry while preserving navigation and proposal actions', () => {
  const container = makeContainer();
  const selected = [];
  let proposalsOpened = 0;
  renderTopbarV2(container, {
    ...baseState,
    proposals: [{ proposal_id: 'proposal-1', summary: '保留待审提案能力' }],
  }, {
    onSelectTool: tool => selected.push(tool),
    onOpenMemory: () => assert.fail('memory display entry must not be reachable from the topbar'),
    onOpenProposals: () => { proposalsOpened += 1; },
  });

  assert.doesNotMatch(container.innerHTML, /unified-open-memory|unified-topbar-memory-btn|记忆 8|待审 2/);
  for (const [id, label] of [['docs', '文档'], ['music', '听歌'], ['shell-dev', '双车道'], ['settings', '设置']]) {
    assert.match(container.innerHTML, new RegExp(`id="unified-tool-${id}"[^>]*>${label}`));
    container.click(`unified-tool-${id}`);
  }
  assert.deepEqual(selected, ['docs', 'music', 'shell-dev', 'settings']);

  assert.match(container.innerHTML, /id="unified-open-proposal"[^>]*>去处理/);
  container.click('unified-open-proposal');
  assert.equal(proposalsOpened, 1);
});

test('unified topbar keeps current-work and optional session entry behavior', () => {
  const container = makeContainer();
  let currentWorkToggles = 0;
  let sessionsOpened = 0;
  renderTopbarV2(container, {
    ...baseState,
    currentWorkOverview: {
      visible: true,
      open: false,
      label: '当前工作',
      bodyHtml: '',
      canExpand: false,
      moreLabel: '',
      loadingMore: false,
    },
  }, {
    onToggleCurrentWorkOverview: () => { currentWorkToggles += 1; },
    onOpenSessions: () => { sessionsOpened += 1; },
  });

  assert.match(container.innerHTML, /id="unified-request-current-work"/);
  assert.match(container.innerHTML, /id="unified-open-sessions"[^>]*>会话 3/);
  assert.doesNotMatch(container.innerHTML, /unified-open-memory|unified-topbar-memory-btn/);
  container.click('unified-request-current-work');
  container.click('unified-open-sessions');
  assert.equal(currentWorkToggles, 1);
  assert.equal(sessionsOpened, 1);
});
