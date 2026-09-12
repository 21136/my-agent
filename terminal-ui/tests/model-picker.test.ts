import assert from 'node:assert/strict';
import {test} from 'node:test';
import {
  isModelPickerTrigger,
  modelPickerStartIndex,
  modelPickerVisibleWindow,
  nextModelPickerIndex,
  parseModelOptions,
} from '../src/model-picker.js';

test('model picker trigger matches bare /model only', () => {
  assert.equal(isModelPickerTrigger('/model'), true);
  assert.equal(isModelPickerTrigger('/MODEL'), true);
  assert.equal(isModelPickerTrigger(' /model '), true);
  assert.equal(isModelPickerTrigger('/model flash'), false);
  assert.equal(isModelPickerTrigger('/clear'), false);
});

test('model options parse from session.init payload', () => {
  const models = parseModelOptions([
    {id: 'deepseek-v4-flash', name: 'Flash', tier: 'flash'},
    {id: '', name: 'bad'},
    {id: 'deepseek-v4-pro', name: 'Pro', tier: 'pro'},
  ]);
  assert.deepEqual(models, [
    {id: 'deepseek-v4-flash', name: 'Flash', tier: 'flash'},
    {id: 'deepseek-v4-pro', name: 'Pro', tier: 'pro'},
  ]);
});

test('model picker navigation wraps and starts on current model', () => {
  const models = [
    {id: 'deepseek-v4-flash', name: 'Flash', tier: 'flash'},
    {id: 'deepseek-v4-pro', name: 'Pro', tier: 'pro'},
  ];
  assert.equal(modelPickerStartIndex(models, 'deepseek-v4-pro'), 1);
  assert.equal(modelPickerStartIndex(models, 'missing'), 0);
  assert.equal(nextModelPickerIndex(1, 1, 2), 0);
  assert.equal(nextModelPickerIndex(0, -1, 2), 1);
});

test('model picker visible window follows the selected index', () => {
  assert.deepEqual(modelPickerVisibleWindow(4, 1, 6), {start: 0, end: 4});
  assert.deepEqual(modelPickerVisibleWindow(8, 0, 6), {start: 0, end: 6});
  assert.deepEqual(modelPickerVisibleWindow(8, 7, 6), {start: 2, end: 8});
  assert.deepEqual(modelPickerVisibleWindow(8, 6, 6), {start: 2, end: 8});
});
