import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const index=readFileSync(new URL('../src/shells/unified/index.ts',import.meta.url),'utf8');
const css=readFileSync(new URL('../src/shells/unified/single-chat-tools.css',import.meta.url),'utf8');
test('single chat keeps legacy sidebar inert and tools in a closable stage above composer',()=>{
 assert.match(index,/<aside class="unified-sidebar" id="unified-sidebar" hidden inert/);
 assert.match(index,/id="unified-tool-region"/);assert.match(index,/id="unified-tool-close"/);
 assert.match(index,/includeCatalog: true/);
 assert.match(index,/renderSettingsSidebar\(section\) \+ renderSettingsPane/);
 assert.match(index,/runtimeTool\.append\(runtimeBadges\)/);
 assert.doesNotMatch(index,/composerDock\.hidden = auxiliaryFocus/);
 assert.match(css,/\.unified-tool-region/);assert.match(css,/grid-template-columns:\s*minmax\(0,\s*1fr\)/);
 assert.match(css,/is-tool-fullpage/);assert.match(index,/is-tool-fullpage/);
});

test('single-chat header passes no session or project creation handlers and shortcuts cannot switch',()=>{
 const refresh=index.match(/function refreshTopbar\(\): void \{([\s\S]*?)sizeCurrentWorkPopover\(\);/)?.[1]??'';
 assert.match(refresh,/openProposals,\s*undefined,\s*\/\/ single continuous conversation[^\n]*\n\s*undefined,\s*\n\s*undefined,\s*\n\s*undefined,/);
 assert.doesNotMatch(index,/if \(key === "[kn]"\)/);
 assert.match(index,/if \(tab === "projects"\) \{[\s\S]*?return;/);
});
