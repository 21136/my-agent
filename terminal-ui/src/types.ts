export type UserBlock = {kind: 'user'; text: string};
export type ThinkingBlock = {kind: 'thinking'; text: string; collapsed?: boolean};
export type AssistantBlock = {kind: 'assistant'; name: string; body: string; turnIndex?: number};
export type AssistantStreamingBlock = {
  kind: 'assistant_streaming';
  name: string;
  body: string;
  turnIndex: number;
};
export type NoticeBlock = {
  kind: 'notice';
  text: string;
  /** Auto-hide after a few seconds (plan gate notices). */
  ephemeral?: boolean;
  shownAt?: number;
};
export type TurnSepBlock = {kind: 'turn_sep'};
export type TerminalBlock =
  | UserBlock
  | ThinkingBlock
  | AssistantBlock
  | AssistantStreamingBlock
  | NoticeBlock
  | TurnSepBlock;
