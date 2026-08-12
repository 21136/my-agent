/**
 * Scheme B (高对比) — source of truth for Terminal Ink UI.
 * @see docs/TERMINAL-MODE.md §6.4.9
 * @see docs/demos/terminal-color-preview.html
 */
export const tokens = {
  bg: '#09090b',

  welcome: {
    border: '#d4a574',
    greet: '#fff7ed',
    sub: '#e7d5c4',
    metaModel: '#fbbf24',
    metaRoot: '#38bdf8',
    metaSep: '#52525b',
  },

  transcript: {
    user: '#22d3ee',
    userBorder: '#06b6d4',
    userBg: '#06b6d41a', // preview/CSS only — Ink: left border, no fill (§6.6.1)


    thinkingBorder: '#818cf8',
    thinkingLabel: '#a5b4fc',
    thinkingText: '#c4b5fd',
    thinkingBg: '#6366f11f', // preview/CSS only

    assistantHeader: '#ffffff',
    assistantDiamond: '#fbbf24',
    assistantBody: '#f4f4f5',
    assistantStrong: '#fef08a',

    path: '#38bdf8',
    codeFg: '#fde047',
    codeBg: '#422006',

    noticeWarn: '#fb923c',
    noticeBg: '#fb923c1a', // preview/CSS only

    turnSep: '#52525b',
  },

  prompt: '#fafafa',

  status: {
    muted: '#71717a',
    model: '#e4e4e7',
    working: '#fbbf24',
    toolName: '#c084fc',
    root: '#38bdf8',
    sep: '#3f3f46',
  },
} as const;

export type Tokens = typeof tokens;
