import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, symlinkSync, linkSync, renameSync } from 'node:fs';
import { existsSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';
import { test } from 'node:test';
import { createManagedSourceJournal, readManagedSourceJournal } from '../../launcher/managed-source-journal.mjs';

// Fixtures are intentionally durable: never remove them, including on test failure.
const sandboxRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
if (path.basename(sandboxRoot) !== 'sandbox' || !existsSync(path.join(sandboxRoot, 'docs/superpowers/plans/2026-10-24-managed-source-transaction.md'))) {
  throw new Error("Refusing fixture creation outside the verified sandbox root: " + sandboxRoot);
}
const fixtureBase = path.join(sandboxRoot, 'artifacts/managed-transaction-20261024/fixtures');
mkdirSync(fixtureBase, { recursive: true });
const runRoot = mkdtempSync(path.join(fixtureBase, 'journal-'));
let serial = 0;
function fixture(name = 'case') {
  const directory = path.join(runRoot, `${String(++serial).padStart(2, '0')}-${name}`);
  mkdirSync(directory, { recursive: true });
  return directory;
}
const identity = { installationId: '11111111-1111-4111-8111-111111111111', namespaceDigest: 'a'.repeat(64) };
const operation = { operationId: '22222222-2222-4222-8222-222222222222', planDigest: 'b'.repeat(64), reviewDigest: 'c'.repeat(64) };
const receipt = {
  mergeCommit: '1'.repeat(40), mergedTree: '2'.repeat(64), stableParent: '3'.repeat(40), sandboxParent: '4'.repeat(64),
  deliveryPaths: ['desktop/a.mjs', 'protocol/b.json'], preservationDigest: 'd'.repeat(64),
};
const filename = (directory) => path.join(directory, 'managed-source-transaction.jsonl');

async function complete(directory) {
  const journal = await createManagedSourceJournal({ directory, identity, operation });
  const token = await journal.beforeWrite({ step: 'source_merge' });
  journal.afterWrite({ writeId: token.writeId, exitCode: 0 });
  journal.finish({ receipt });
  journal.close();
  return token;
}
test('claims a fixed journal exclusively across processes and remains single-use after success', async () => {
  const directory = fixture('exclusive');
  const first = await createManagedSourceJournal({ directory, identity, operation });
  const child = spawnSync(process.execPath, ['--input-type=module', '-e', `import {createManagedSourceJournal} from ${JSON.stringify(new URL('../../launcher/managed-source-journal.mjs', import.meta.url).href)}; try { await createManagedSourceJournal({directory:${JSON.stringify(directory)},identity:${JSON.stringify(identity)},operation:${JSON.stringify(operation)}}); process.exit(7) } catch (error) { process.exit(String(error.message).includes('EEXIST') ? 0 : 8) }`], { encoding: 'utf8' });
  assert.equal(child.status, 0, child.stderr);
  first.close();
  const successDirectory = fixture('single-use-success');
  await complete(successDirectory);
  assert.throws(() => createManagedSourceJournal({ directory: successDirectory, identity, operation }));
});

test('persists and fsyncs intent before returning the one-use write token', async () => {
  const directory = fixture('intent');
  const journal = await createManagedSourceJournal({ directory, identity, operation });
  const token = await journal.beforeWrite({ step: 'source_merge' });
  const lines = readFileSync(filename(directory), 'utf8').trimEnd().split('\n').map(JSON.parse);
  assert.equal(lines.at(-1).type, 'write_intent');
  assert.deepEqual(lines.at(-1).payload, { step: 'source_merge', operationId: operation.operationId, writeId: token.writeId });
  assert.equal(token.operationId, operation.operationId);
  assert.throws(() => journal.beforeWrite({ step: 'source_merge' }));
  journal.close();
});

test('accepts only ordered receipt with exact fields and returns verified status on inspection', async () => {
  const directory = fixture('receipt-order');
  const journal = await createManagedSourceJournal({ directory, identity, operation });
  const token = await journal.beforeWrite({ step: 'source_merge' });
  assert.throws(() => journal.finish({ receipt }), /exit|order|state/i);
  journal.afterWrite({ writeId: token.writeId, exitCode: 0 });
  assert.throws(() => journal.finish({ receipt: { ...receipt, ready: true } }), /receipt|field|key/i);
  journal.finish({ receipt });
  journal.close();
  const read = await readManagedSourceJournal({ directory, identity });
  assert.equal(read.status, 'source_merge_verified');
  assert.deepEqual(read.receipt, receipt);
  assert.deepEqual(Object.keys(read), ['identity', 'operation', 'events', 'status', 'receipt']);
});

test('rejects duplicate finish and invalid write token without changing verified state', async () => {
  const directory = fixture('token');
  const journal = await createManagedSourceJournal({ directory, identity, operation });
  const token = await journal.beforeWrite({ step: 'source_merge' });
  assert.throws(() => journal.afterWrite({ writeId: '33333333-3333-4333-8333-333333333333', exitCode: 0 }), /token|write/i);
  journal.afterWrite({ writeId: token.writeId, exitCode: 0 });
  journal.finish({ receipt });
  assert.throws(() => journal.finish({ receipt }), /state|finish|closed/i);
  journal.close();
  assert.equal((await readManagedSourceJournal({ directory, identity })).status, 'source_merge_verified');
});

test('nonzero exit requires maintenance and never permits a second write', async () => {
  const directory = fixture('nonzero');
  const journal = await createManagedSourceJournal({ directory, identity, operation });
  const token = await journal.beforeWrite({ step: 'source_merge' });
  journal.afterWrite({ writeId: token.writeId, exitCode: 2 });
  assert.throws(() => journal.beforeWrite({ step: 'source_merge' }), /state|maintenance|write/i);
  journal.close();
  const read = await readManagedSourceJournal({ directory, identity });
  assert.equal(read.status, 'maintenance_required');
  assert.equal(read.receipt, null);
});

test('inspection of an unfinished intent is maintenance and cannot reopen execution', async () => {
  const directory = fixture('unfinished');
  const journal = await createManagedSourceJournal({ directory, identity, operation });
  journal.beforeWrite({ step: 'source_merge' });
  journal.close();
  assert.equal((await readManagedSourceJournal({ directory, identity })).status, 'maintenance_required');
  assert.throws(() => createManagedSourceJournal({ directory, identity, operation }));
});

test('validates every hash-chain link and rejects truncated or malformed tails', async (t) => {
  for (const [name, mutate] of [
    ['truncated', (text) => text.slice(0, -3)],
    ['broken-link', (text) => { const rows = text.trimEnd().split('\n').map(JSON.parse); rows[1].previousHash = 'f'.repeat(64); return `${rows.map((row) => JSON.stringify(row)).join('\n')}\n`; }],
    ['oversized', () => `${'x'.repeat(1024 * 1024 + 1)}\n`],
  ]) {
    const directory = fixture(name);
    await complete(directory);
    writeFileSync(filename(directory), mutate(readFileSync(filename(directory), 'utf8')));
    assert.throws(() => readManagedSourceJournal({ directory, identity }), /journal|chain|trunc|size|corrupt|hash/i, name);
  }
});

function isolatedNode(source, timeout = 5000) {
  return spawnSync(process.execPath, ['--input-type=module', '-e', source], { encoding: 'utf8', timeout });
}
const journalModuleUrl = pathToFileURL(path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../launcher/managed-source-journal.mjs')).href;

for (const failure of ['zero-write', 'fsync-throws']) {
  test(`poisons the live handle after ${failure} in an isolated process`, () => {
    const directory = fixture(failure);
    const script = `
      import { createRequire } from 'node:module';
      const fs = createRequire(import.meta.url)('node:fs');
      import { syncBuiltinESMExports } from 'node:module';
      const { createManagedSourceJournal } = await import(${JSON.stringify(journalModuleUrl)});
      const journal = createManagedSourceJournal({ directory: ${JSON.stringify(directory)}, identity: ${JSON.stringify(identity)}, operation: ${JSON.stringify(operation)} });
      const original = fs.${failure === 'zero-write' ? 'writeSync' : 'fsyncSync'};
      fs.${failure === 'zero-write' ? 'writeSync' : 'fsyncSync'} = ${failure === 'zero-write' ? '() => 0' : "() => { throw new Error('simulated fsync failure') }"};
      syncBuiltinESMExports();
      let failed = false;
      try { journal.beforeWrite({ step: 'source_merge' }); } catch (error) { failed = /persistence|write/i.test(error.message); }
      fs.${failure === 'zero-write' ? 'writeSync' : 'fsyncSync'} = original;
      syncBuiltinESMExports();
      let poisoned = false;
      try { journal.beforeWrite({ step: 'source_merge' }); } catch (error) { poisoned = /poison/i.test(error.message); }
      journal.close();
      process.exit(failed && poisoned ? 0 : 31);
    `;
    const result = isolatedNode(script);
    assert.equal(result.status, 0, result.stderr || result.stdout);
    assert.equal(result.error, undefined, result.error?.message);
  });
}

test('rejects same-size FD mutation during bounded inspection using stable timestamps', async () => {
  const directory = fixture('read-drift');
  await complete(directory);
  const file = filename(directory);
  const script = `
    import { createRequire } from 'node:module';
      const fs = createRequire(import.meta.url)('node:fs');
    import { syncBuiltinESMExports } from 'node:module';
    const { readManagedSourceJournal } = await import(${JSON.stringify(journalModuleUrl)});
    const originalRead = fs.readSync;
    const originalWrite = fs.writeSync;
    const originalOpen = fs.openSync;
    const originalClose = fs.closeSync;
    let mutated = false;
    fs.readSync = (...args) => {
      const count = originalRead(...args);
      if (!mutated && count > 0) {
        mutated = true;
        const target = originalOpen(${JSON.stringify(file)}, 'r+');
        originalWrite(target, Buffer.from('X'), 0, 1, 0);
        fs.fsyncSync(target);
        originalClose(target);
        fs.utimesSync(${JSON.stringify(file)}, new Date('2001-01-01T00:00:00Z'), new Date('2001-01-01T00:00:00Z'));
      }
      return count;
    };
    syncBuiltinESMExports();
    try { readManagedSourceJournal({ directory: ${JSON.stringify(directory)}, identity: ${JSON.stringify(identity)} }); process.exit(32); }
    catch (error) { process.exit(mutated && error.message === 'Journal changed during bounded read' ? 0 : 33); }
  `;
  const result = isolatedNode(script);
  assert.equal(result.status, 0, result.stderr || result.stdout);
  assert.equal(result.error, undefined, result.error?.message);
});

test('rejects empty delivery path receipts', async () => {
  const directory = fixture('empty-receipt-paths');
  const journal = createManagedSourceJournal({ directory, identity, operation });
  const token = journal.beforeWrite({ step: 'source_merge' });
  journal.afterWrite({ writeId: token.writeId, exitCode: 0 });
  assert.throws(() => journal.finish({ receipt: { ...receipt, deliveryPaths: [] } }), /deliveryPaths|receipt/i);
  journal.close();
});

test('detects active journal truncation, edit, and path replacement before subsequent appends', async (t) => {
  {
    const directory = fixture('drift-before-intent');
    const journal = createManagedSourceJournal({ directory, identity, operation });
    writeFileSync(filename(directory), '');
    assert.throws(() => journal.beforeWrite({ step: 'source_merge' }), /drift|changed|size|journal/i);
    journal.close();
    assert.throws(() => readManagedSourceJournal({ directory, identity }));
  }
  {
    const directory = fixture('drift-before-exit');
    const journal = createManagedSourceJournal({ directory, identity, operation });
    const token = journal.beforeWrite({ step: 'source_merge' });
    const file = filename(directory);
    const text = readFileSync(file, 'utf8');
    writeFileSync(file, `${text[0] === ' ' ? 'x' : ' '}${text.slice(1)}`);
    assert.throws(() => journal.afterWrite({ writeId: token.writeId, exitCode: 0 }), /drift|changed|contents|journal/i);
    journal.close();
    assert.throws(() => readManagedSourceJournal({ directory, identity }));
  }
  {
    const directory = fixture('drift-before-finish');
    const journal = createManagedSourceJournal({ directory, identity, operation });
    const token = journal.beforeWrite({ step: 'source_merge' });
    journal.afterWrite({ writeId: token.writeId, exitCode: 0 });
    const file = filename(directory);
    const replacement = path.join(directory, 'replacement.jsonl');
    writeFileSync(replacement, '');
    try {
      const displaced = path.join(directory, 'displaced.jsonl');
      renameSync(file, displaced);
      renameSync(replacement, file);
    } catch (error) {
      t.skip('filesystem does not allow replacing an open journal: ' + error.code);
      journal.close();
      return;
    }
    assert.throws(() => journal.finish({ receipt }), /drift|changed|identity|journal/i);
    journal.close();
    assert.throws(() => readManagedSourceJournal({ directory, identity }));
  }
});
test('rejects identity substitution and operation identity corruption', async () => {
  const directory = fixture('identity');
  await complete(directory);
  assert.throws(() => readManagedSourceJournal({ directory, identity: { ...identity, namespaceDigest: 'e'.repeat(64) } }), /identity|namespace/i);
  const rows = readFileSync(filename(directory), 'utf8').trimEnd().split('\n').map(JSON.parse);
  rows[0].payload.operation.operationId = '55555555-5555-4555-8555-555555555555';
  writeFileSync(filename(directory), `${rows.map((row) => JSON.stringify(row)).join('\n')}\n`);
  assert.throws(() => readManagedSourceJournal({ directory, identity }), /hash|corrupt|operation|journal/i);
});

test('rejects symlinked directory ancestors and symlink or hardlinked journal files', async (t) => {
  const real = fixture('linked-target');
  const alias = path.join(runRoot, 'linked-alias');
  try { symlinkSync(real, alias, 'junction'); } catch (error) { if (['EPERM', 'EISDIR', 'ENOTSUP', 'EINVAL'].includes(error.code)) { t.skip('filesystem does not support reparse fixtures: ' + error.code); return; } throw error; }
  await assert.rejects(createManagedSourceJournal({ directory: alias, identity, operation }), /link|alias|reparse|directory/i);
  const symlinkFileDir = fixture('symlink-file');
  const target = path.join(symlinkFileDir, 'target');
  writeFileSync(target, 'not a journal');
  symlinkSync(target, filename(symlinkFileDir), 'file');
  await assert.rejects(readManagedSourceJournal({ directory: symlinkFileDir, identity }), /link|regular|file|journal/i);
  const hardlinkDir = fixture('hardlink-file');
  const hardlinkTarget = path.join(hardlinkDir, 'target');
  writeFileSync(hardlinkTarget, 'not a journal');
  linkSync(hardlinkTarget, filename(hardlinkDir));
  await assert.rejects(readManagedSourceJournal({ directory: hardlinkDir, identity }), /link|hard|regular|file|journal/i);
});










