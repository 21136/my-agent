export type TerminalInputKey = {
  return?: boolean;
  backspace?: boolean;
  delete?: boolean;
  ctrl?: boolean;
  meta?: boolean;
};

export type TerminalInputAction =
  | {type: 'none'}
  | {type: 'submit'; text: string}
  | {type: 'confirm'; choice: string}
  | {type: 'cancel'};

export type TerminalInputState = {
  text: string;
};

export function reduceTerminalInput(
  state: TerminalInputState,
  input: string,
  key: TerminalInputKey,
  confirm?: {allowApproveAll: boolean},
): {state: TerminalInputState; action: TerminalInputAction} {
  if (confirm) {
    const choice = input.trim().toLowerCase();
    if (choice === 'y' || choice === 'n' || (choice === 'a' && confirm.allowApproveAll)) {
      return {state, action: {type: 'confirm', choice}};
    }
    return {state, action: {type: 'none'}};
  }

  if (key.ctrl && input.toLowerCase() === 'c') {
    return {state, action: {type: 'cancel'}};
  }
  if (key.return) {
    const text = state.text;
    return {
      state: {text},
      action: text.trim() ? {type: 'submit', text} : {type: 'none'},
    };
  }
  if (key.backspace || key.delete) {
    return {state: {text: state.text.slice(0, -1)}, action: {type: 'none'}};
  }
  if (!key.ctrl && !key.meta && input) {
    return {state: {text: state.text + input}, action: {type: 'none'}};
  }
  return {state, action: {type: 'none'}};
}
