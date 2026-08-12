/** Parse xterm SGR / legacy mouse wheel sequences from raw TTY input. */

const SGR_MOUSE_RE = /\x1b\[<(\d+);(\d*);(\d*)([mM])/g;
const LEGACY_MOUSE_RE = /\x1b\[M([\s\S]{3})/g;

/** Wheel-up / wheel-down button codes (xterm SGR and legacy). */
const WHEEL_UP_BUTTONS = new Set([64, 616]);
const WHEEL_DOWN_BUTTONS = new Set([65, 617]);

export function parseMouseWheelNotches(input: string): {rest: string; notches: number} {
  let notches = 0;

  let rest = input.replace(SGR_MOUSE_RE, (_match, buttonRaw) => {
    const button = Number(buttonRaw);
    if (WHEEL_UP_BUTTONS.has(button)) notches += 1;
    else if (WHEEL_DOWN_BUTTONS.has(button)) notches -= 1;
    return '';
  });

  rest = rest.replace(LEGACY_MOUSE_RE, (_match, payload: string) => {
    const button = payload.charCodeAt(0) - 32;
    if (WHEEL_UP_BUTTONS.has(button)) notches += 1;
    else if (WHEEL_DOWN_BUTTONS.has(button)) notches -= 1;
    return '';
  });

  return {rest, notches};
}

export function wheelLinesPerNotch(viewportRows: number): number {
  const env = process.env.MY_AGENT_TERMINAL_WHEEL_LINES?.trim();
  if (env && /^\d+$/.test(env)) return Math.max(1, Number(env));
  return Math.max(3, Math.floor(viewportRows / 3));
}

export function enableMouseWheelTracking(write: (data: string) => void): void {
  // 1000 = click reporting; 1006 = SGR coordinates (wheel uses buttons 64/65).
  write('\x1b[?1000h\x1b[?1006h');
}

export function disableMouseWheelTracking(write: (data: string) => void): void {
  write('\x1b[?1006l\x1b[?1000l');
}

export function isMouseWheelEnabled(): boolean {
  const raw = process.env.MY_AGENT_TERMINAL_MOUSE?.trim().toLowerCase() ?? '';
  if (['1', 'on', 'true', 'yes'].includes(raw)) return true;
  if (['0', 'off', 'false', 'no'].includes(raw)) return false;
  return true;
}
