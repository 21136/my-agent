import assert from 'node:assert/strict';
import {test} from 'node:test';
import {reduceTerminalInput} from '../src/input.js';

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
