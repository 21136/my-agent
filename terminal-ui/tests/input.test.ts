import assert from 'node:assert/strict';
import {test} from 'node:test';
import {reduceTerminalInput} from '../src/input.js';
import {
  appendInputHistory,
  navigateInputHistory,
} from '../src/input-history.js';

test('terminal input appends printable text and handles backspace', () => {
  let result = reduceTerminalInput({text: ''}, '你好', {});
  assert.deepEqual(result, {state: {text: '你好'}, action: {type: 'none'}});
  result = reduceTerminalInput(result.state, '', {backspace: true});
  assert.deepEqual(result, {state: {text: '你'}, action: {type: 'none'}});
});

test('terminal input submits text and clears the composer', () => {
  const result = reduceTerminalInput({text: '  hello  '}, '', {return: true});
  assert.deepEqual(result, {state: {text: '  hello  '}, action: {type: 'submit', text: '  hello  '}});
});

test('terminal input ignores empty submit', () => {
  const result = reduceTerminalInput({text: '  '}, '', {return: true});
  assert.deepEqual(result, {state: {text: '  '}, action: {type: 'none'}});
});

test('confirm accepts only allowed choices', () => {
  const confirm = {allowApproveAll: true};
  assert.deepEqual(reduceTerminalInput({text: 'pending'}, 'Y', {}, confirm).action, {type: 'confirm', choice: 'y'});
  assert.deepEqual(reduceTerminalInput({text: 'pending'}, 'a', {}, confirm).action, {type: 'confirm', choice: 'a'});
  assert.deepEqual(reduceTerminalInput({text: 'pending'}, 'x', {}, confirm).action, {type: 'none'});
});

test('ctrl-c emits cancel without changing input', () => {
  assert.deepEqual(
    reduceTerminalInput({text: 'draft'}, 'c', {ctrl: true}),
    {state: {text: 'draft'}, action: {type: 'cancel'}},
  );
});

test('ctrl-d emits exit without changing input', () => {
  assert.deepEqual(
    reduceTerminalInput({text: ''}, 'd', {ctrl: true}),
    {state: {text: ''}, action: {type: 'exit'}},
  );
  assert.deepEqual(
    reduceTerminalInput({text: 'draft'}, 'd', {ctrl: true}),
    {state: {text: 'draft'}, action: {type: 'exit'}},
  );
});

test('ctrl-d does not exit a confirmation prompt', () => {
  assert.deepEqual(
    reduceTerminalInput({text: ''}, 'd', {ctrl: true}, {allowApproveAll: false}).action,
    {type: 'none'},
  );
});

test('escape emits cancel for a working turn and a confirmation', () => {
  assert.deepEqual(reduceTerminalInput({text: ''}, '', {escape: true}).action, {type: 'cancel'});
  assert.deepEqual(
    reduceTerminalInput({text: ''}, '', {escape: true}, {allowApproveAll: false}).action,
    {type: 'cancel'},
  );
});

test('input history deduplicates consecutive submissions and restores the draft', () => {
  const history = appendInputHistory([], 'first');
  assert.deepEqual(appendInputHistory(history, 'first'), ['first']);
  const next = appendInputHistory(history, 'second');
  const up = navigateInputHistory(next, {index: -1, draft: 'draft'}, -1, 'draft');
  assert.deepEqual(up, {cursor: {index: 1, draft: 'draft'}, text: 'second'});
  const older = navigateInputHistory(next, up.cursor, -1, up.text);
  assert.deepEqual(older, {cursor: {index: 0, draft: 'draft'}, text: 'first'});
  const newer = navigateInputHistory(next, older.cursor, 1, older.text);
  assert.deepEqual(newer, {cursor: {index: 1, draft: 'draft'}, text: 'second'});
  const restored = navigateInputHistory(next, newer.cursor, 1, newer.text);
  assert.deepEqual(restored, {cursor: {index: -1, draft: ''}, text: 'draft'});
});
