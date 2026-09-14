import assert from 'node:assert/strict';
import {test} from 'node:test';
import {
  completeSlashCommand,
  nextSlashCommandIndex,
  slashCommandCandidates,
} from '../src/slash-commands.js';

test('slash command candidates filter by prefix and close after an exact command', () => {
  assert.deepEqual(slashCommandCandidates('/').map((command) => command.name), [
    '/model',
    '/clear',
    '/compact',
  ]);
  assert.deepEqual(slashCommandCandidates('/co').map((command) => command.name), ['/compact']);
  assert.deepEqual(slashCommandCandidates('/compact'), []);
  assert.deepEqual(slashCommandCandidates('/model flash'), []);
});

test('slash command navigation wraps and model completion leaves an argument slot', () => {
  assert.equal(nextSlashCommandIndex(0, -1, 3), 2);
  assert.equal(nextSlashCommandIndex(2, 1, 3), 0);
  const model = slashCommandCandidates('/mo')[0];
  assert.ok(model);
  assert.equal(completeSlashCommand(model), '/model ');
});
