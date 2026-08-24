export type InputHistoryCursor = {
  index: number;
  draft: string;
};

export const MAX_INPUT_HISTORY = 100;

export function appendInputHistory(history: readonly string[], text: string): string[] {
  if (!text.trim() || history.at(-1) === text) return [...history];
  return [...history, text].slice(-MAX_INPUT_HISTORY);
}

export function navigateInputHistory(
  history: readonly string[],
  cursor: InputHistoryCursor,
  direction: -1 | 1,
  currentText: string,
): {cursor: InputHistoryCursor; text: string} {
  if (history.length === 0) return {cursor, text: currentText};

  if (direction < 0) {
    const index = cursor.index < 0 ? history.length - 1 : Math.max(0, cursor.index - 1);
    return {
      cursor: {
        index,
        draft: cursor.index < 0 ? currentText : cursor.draft,
      },
      text: history[index] ?? currentText,
    };
  }

  if (cursor.index < 0) return {cursor, text: currentText};
  if (cursor.index >= history.length - 1) {
    return {cursor: {index: -1, draft: ''}, text: cursor.draft};
  }

  const index = cursor.index + 1;
  return {
    cursor: {index, draft: cursor.draft},
    text: history[index] ?? cursor.draft,
  };
}
