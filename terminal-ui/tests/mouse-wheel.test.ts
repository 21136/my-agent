import assert from 'node:assert/strict';
import test from 'node:test';
import {parseMouseWheelNotches, wheelLinesPerNotch} from '../src/input/mouse-wheel.js';

test('parseMouseWheelNotches reads SGR wheel up and down', () => {
  const up = parseMouseWheelNotches('\x1b[<64;12;5M');
  assert.equal(up.notches, 1);
  assert.equal(up.rest, '');

  const down = parseMouseWheelNotches('\x1b[<65;12;5M');
  assert.equal(down.notches, -1);
  assert.equal(down.rest, '');
});

test('parseMouseWheelNotches reads legacy wheel events', () => {
  const up = parseMouseWheelNotches(`\x1b[M${String.fromCharCode(96, 40, 10)}`);
  assert.equal(up.notches, 1);

  const down = parseMouseWheelNotches(`\x1b[M${String.fromCharCode(97, 40, 10)}`);
  assert.equal(down.notches, -1);
});

test('parseMouseWheelNotches strips wheel and keeps keyboard input', () => {
  const mixed = parseMouseWheelNotches(`\x1b[<64;1;1Ma`);
  assert.equal(mixed.notches, 1);
  assert.equal(mixed.rest, 'a');
});

test('wheelLinesPerNotch scales with viewport height', () => {
  assert.equal(wheelLinesPerNotch(30), 10);
  assert.equal(wheelLinesPerNotch(8), 3);
});
