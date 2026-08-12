import React from 'react';
import {Text} from 'ink';

const CELL_RE =
  /\x1b\[38;2;(\d+);(\d+);(\d+)m\x1b\[48;2;(\d+);(\d+);(\d+)m(.)\x1b\[0m/g;

function rgbHex(r: number, g: number, b: number): string {
  const h = (n: number) => n.toString(16).padStart(2, '0');
  return `#${h(r)}${h(g)}${h(b)}`;
}

/** Render one welcome mascot row (truecolor half-block ANSI from welcome_mascot_data). */
export function AnsiSpriteLine({line}: {line: string}) {
  const cells: React.ReactNode[] = [];
  let match: RegExpExecArray | null;
  let index = 0;
  CELL_RE.lastIndex = 0;
  while ((match = CELL_RE.exec(line)) !== null) {
    const [, fr, fg, fb, br, bg, bb, ch] = match;
    cells.push(
      <Text
        key={index}
        color={rgbHex(Number(fr), Number(fg), Number(fb))}
        backgroundColor={rgbHex(Number(br), Number(bg), Number(bb))}
      >
        {ch}
      </Text>,
    );
    index += 1;
  }
  if (cells.length === 0) return null;
  return <Text>{cells}</Text>;
}
