import test from 'node:test';
import assert from 'node:assert/strict';
import {MASCOT_LINES, MASCOT_WIDTH} from '../src/theme/welcomeMascotData.js';

const CELL_RE =
  /\x1b\[38;2;(\d+);(\d+);(\d+)m\x1b\[48;2;(\d+);(\d+);(\d+)m(.)\x1b\[0m/g;

function countCells(line: string): number {
  let n = 0;
  CELL_RE.lastIndex = 0;
  while (CELL_RE.exec(line) !== null) n += 1;
  return n;
}

test('welcome mascot lines match legacy sprite width', () => {
  assert.ok(MASCOT_LINES.length > 10);
  for (const line of MASCOT_LINES) {
    assert.equal(countCells(line), MASCOT_WIDTH, 'each row should be 32 half-blocks');
  }
});
