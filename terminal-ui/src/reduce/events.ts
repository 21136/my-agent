import type {TerminalBlock, TerminalResult} from '../types.js';
import {isEphemeralPlanNotice} from '../repl/committed-blocks.js';
import {parseModelOptions, type ModelOption} from '../model-picker.js';

export const DEFAULT_ASSISTANT_NAME = '打工仔';
const DEFAULT_GREET = '下午好。';
const DEFAULT_MODEL = 'flash';
const DEFAULT_ROOT = '.';
const EXECUTION_FINAL_STATES = new Set(['settled', 'paused', 'failed']);

type BaseEvent = {type: string; [key: string]: unknown};
export type TerminalEvent = BaseEvent;
export type ExecutionStateName =
  | 'idle'
  | 'queued'
  | 'running'
  | 'stopping'
  | 'settled'
  | 'paused'
  | 'failed';
export type TerminalUiState = {
  greet: string;
  greetSub: string;
  model: string;
  root: string;
  mascotLines: string[];
  mascotLabel: string;
  models: ModelOption[];
  blocks: TerminalBlock[];
  working: boolean;
  turnIndex: number;
  assistantBuffer: string;
  activeTool?: string;
  /** Wall-clock ms when the current tool became active (client-side). */
  activeToolStartedAt?: number;
  /** Terminal Plan-and-Execute status segment (T-5733). */
  planStatus?: string;
  /** Live activity for status bar (tool progress / waiting labels). */
  activityText?: string;
  result?: TerminalResult;
  confirm?: {requestId: string; preview: string; allowApproveAll: boolean};
  executionRunId?: string;
  executionState: ExecutionStateName;
  executionSequence: number;
};

export function createInitialState(overrides: Partial<TerminalUiState> = {}): TerminalUiState {
  return {
    greet: overrides.greet ?? DEFAULT_GREET,
    greetSub: overrides.greetSub ?? '',
    model: overrides.model ?? DEFAULT_MODEL,
    root: overrides.root ?? DEFAULT_ROOT,
    mascotLines: overrides.mascotLines ?? [],
    mascotLabel: overrides.mascotLabel ?? DEFAULT_ASSISTANT_NAME,
    models: overrides.models ? [...overrides.models] : [],
    blocks: overrides.blocks ? [...overrides.blocks] : [],
    working: overrides.working ?? false,
    turnIndex: overrides.turnIndex ?? 0,
    assistantBuffer: overrides.assistantBuffer ?? '',
    activeTool: overrides.activeTool,
    activeToolStartedAt: overrides.activeToolStartedAt,
    planStatus: overrides.planStatus ?? '',
    activityText: overrides.activityText ?? '',
    result: overrides.result,
    confirm: overrides.confirm,
    executionRunId: overrides.executionRunId,
    executionState: overrides.executionState ?? 'idle',
    executionSequence: overrides.executionSequence ?? 0,
  };
}

function classifyNotice(
  text: string,
  level: unknown,
): {text: string; tone: 'warning' | 'error' | 'cancelled'; result?: TerminalResult} {
  const normalized = text.trim();
  const normalizedLevel = typeof level === 'string' ? level.toLowerCase() : '';
  if (normalizedLevel === 'cancelled' || /^\(?cancelled\)?$/i.test(normalized)) {
    return {
      text: '已取消本轮执行',
      tone: 'cancelled',
      result: {kind: 'cancelled', text: '已取消本轮执行'},
    };
  }
  if (normalizedLevel === 'error' || /^(?:llm )?error\s*:/i.test(normalized)) {
    const reason = normalized.replace(/^(?:llm )?error\s*:\s*/i, '').trim();
    const displayText = `失败 · ${reason || '执行失败'}`;
    return {
      text: displayText,
      tone: 'error',
      result: {kind: 'failed', text: displayText},
    };
  }
  return {text: normalized, tone: 'warning'};
}

function dropEmptyTrailingThinking(blocks: TerminalBlock[]): TerminalBlock[] {
  const last = blocks.at(-1);
  if (last?.kind === 'thinking' && !last.text.trim()) {
    return blocks.slice(0, -1);
  }
  return blocks;
}

function collapseTrailingThinking(blocks: TerminalBlock[]): TerminalBlock[] {
  const last = blocks.at(-1);
  if (last?.kind !== 'thinking') return blocks;
  if (!last.text.trim()) return dropEmptyTrailingThinking(blocks);
  return [...blocks.slice(0, -1), {kind: 'thinking', text: last.text, collapsed: true}];
}

function appendThinking(blocks: TerminalBlock[], text: string): TerminalBlock[] {
  if (!text) return blocks;
  const next = [...blocks];
  const last = next.at(-1);
  if (last?.kind === 'thinking') {
    next[next.length - 1] = {kind: 'thinking', text: last.text + text, collapsed: false};
    return next;
  }
  next.push({kind: 'thinking', text, collapsed: false});
  return next;
}

function appendAssistant(
  blocks: TerminalBlock[],
  text: string,
  name: string,
  turnIndex: number,
): TerminalBlock[] {
  if (!text) return blocks;
  const next = [...blocks];
  const last = next.at(-1);
  if (last?.kind === 'assistant_streaming' && last.turnIndex === turnIndex) {
    next[next.length - 1] = {...last, body: last.body + text};
    return next;
  }
  next.push({kind: 'assistant_streaming', name, body: text, turnIndex});
  return next;
}

function finalizeAssistant(
  blocks: TerminalBlock[],
  finalText: unknown,
  name: string,
  turnIndex: number,
  assistantBuffer: string,
): TerminalBlock[] {
  const text = typeof finalText === 'string' && finalText ? finalText : assistantBuffer;
  const next = blocks.filter(
    (block) => !(block.kind === 'assistant_streaming' && block.turnIndex === turnIndex),
  );
  if (!text) return next;
  const last = next.at(-1);
  if (last?.kind === 'assistant' && last.turnIndex === turnIndex && last.body === text) return next;
  next.push({kind: 'assistant', name, body: text, turnIndex});
  return next;
}

function eventRunId(event: BaseEvent): string | undefined {
  return typeof event.run_id === 'string' && event.run_id.trim() ? event.run_id.trim() : undefined;
}

function isStaleRunId(event: BaseEvent, currentRunId: string | undefined): boolean {
  const runId = eventRunId(event);
  return Boolean(runId && currentRunId && runId !== currentRunId);
}

export function reduceState(
  state: TerminalUiState,
  event: TerminalEvent,
  turnCount = 0,
): {state: TerminalUiState; turnCount: number} {
  let blocks = [...state.blocks];
  let greet = state.greet;
  let greetSub = state.greetSub;
  let model = state.model;
  let root = state.root;
  let mascotLines = state.mascotLines;
  let mascotLabel = state.mascotLabel;
  let models = state.models;
  let working = state.working;
  let turnIndex = state.turnIndex;
  let assistantBuffer = state.assistantBuffer;
  let activeTool = state.activeTool;
  let activeToolStartedAt = state.activeToolStartedAt;
  let planStatus = state.planStatus ?? '';
  let activityText = state.activityText ?? '';
  let result = state.result;
  let confirm = state.confirm;
  let executionRunId = state.executionRunId;
  let executionState = state.executionState;
  let executionSequence = state.executionSequence;
  let turns = turnCount;
  switch (event.type) {
    case 'session.init':
      if (typeof event.greet === 'string') greet = event.greet;
      if (typeof event.greetSub === 'string') greetSub = event.greetSub;
      if (typeof event.model === 'string') model = event.model;
      if (typeof event.root === 'string') root = event.root;
      if (Array.isArray(event.mascotLines)) {
        mascotLines = event.mascotLines.filter((line): line is string => typeof line === 'string');
      }
      if (typeof event.mascotLabel === 'string') mascotLabel = event.mascotLabel;
      if (Array.isArray(event.models)) models = parseModelOptions(event.models);
      break;
    case 'turn.start': {
      if (isStaleRunId(event, executionRunId)) break;
      const runId = eventRunId(event);
      if (runId) executionRunId = runId;
      if (turns > 0) blocks = [...blocks, {kind: 'turn_sep'}];
      turns += 1;
      turnIndex = turns;
      assistantBuffer = '';
      activityText = '';
      result = undefined;
      working = true;
      blocks = [...blocks, {kind: 'thinking', text: '', collapsed: false}];
      break;
    }
    case 'execution.state': {
      const sequence =
        typeof event.sequence === 'number' && Number.isFinite(event.sequence)
          ? event.sequence
          : executionSequence + 1;
      if (sequence < executionSequence) break;
      if (sequence === executionSequence && isStaleRunId(event, executionRunId)) break;
      const rawState = event.state;
      if (
        rawState !== 'queued' &&
        rawState !== 'running' &&
        rawState !== 'stopping' &&
        rawState !== 'settled' &&
        rawState !== 'paused' &&
        rawState !== 'failed'
      ) {
        break;
      }
      if (typeof event.run_id === 'string' && event.run_id.trim()) {
        executionRunId = event.run_id.trim();
      }
      executionState = rawState;
      executionSequence = sequence;
      if (EXECUTION_FINAL_STATES.has(rawState)) {
        working = false;
        activeTool = undefined;
        activeToolStartedAt = undefined;
        activityText = '';
      } else {
        working = true;
        activityText = rawState === 'queued' ? '等待续接…' : rawState === 'stopping' ? '正在停止…' : activityText;
      }
      break;
    }
    case 'user.message': {
      const text = typeof event.text === 'string' ? event.text.trim() : '';
      if (text) blocks = [...blocks, {kind: 'user', text}];
      break;
    }
    case 'reasoning.delta':
      if (isStaleRunId(event, executionRunId)) break;
      activityText = '';
      blocks = appendThinking(blocks, typeof event.text === 'string' ? event.text : '');
      break;
    case 'activity.update': {
      if (isStaleRunId(event, executionRunId)) break;
      const text = typeof event.text === 'string' ? event.text.trim() : '';
      if (text) activityText = text;
      break;
    }
    case 'assistant.delta': {
      if (isStaleRunId(event, executionRunId)) break;
      const text = typeof event.text === 'string' ? event.text : '';
      assistantBuffer += text;
      blocks = collapseTrailingThinking(blocks);
      blocks = appendAssistant(blocks, text, DEFAULT_ASSISTANT_NAME, turnIndex);
      break;
    }
    case 'assistant.done': {
      if (isStaleRunId(event, executionRunId)) break;
      const name = typeof event.name === 'string' ? event.name : DEFAULT_ASSISTANT_NAME;
      blocks = collapseTrailingThinking(blocks);
      blocks = finalizeAssistant(blocks, event.text, name, turnIndex, assistantBuffer);
      assistantBuffer = '';
      break;
    }
    case 'notice': {
      const text = typeof event.text === 'string' ? event.text.trim() : '';
      if (text) {
        const classification = classifyNotice(text, event.level);
        blocks = dropEmptyTrailingThinking(blocks);
        const ephemeral = isEphemeralPlanNotice(text);
        blocks = [
          ...blocks,
          {
            kind: 'notice',
            text: classification.text,
            ...(classification.tone !== 'warning' ? {tone: classification.tone} : {}),
            ...(ephemeral ? {ephemeral: true, shownAt: Date.now()} : {}),
          },
        ];
        if (classification.result) result = classification.result;
      }
      break;
    }
    case 'tool.active':
      if (isStaleRunId(event, executionRunId)) break;
      activeTool = typeof event.name === 'string' ? event.name : undefined;
      activeToolStartedAt =
        typeof event.started_at === 'number' && Number.isFinite(event.started_at)
          ? event.started_at
          : Date.now();
      if (activeTool) activityText = `${activeTool} · 执行中…`;
      break;
    case 'tool.clear':
      if (isStaleRunId(event, executionRunId)) break;
      activeTool = undefined;
      activeToolStartedAt = undefined;
      activityText = '';
      break;
    case 'tool.progress': {
      if (isStaleRunId(event, executionRunId)) break;
      const text = typeof event.text === 'string' ? event.text.trim() : '';
      const tool =
        (typeof event.tool === 'string' && event.tool.trim()) || activeTool || 'tool';
      if (text) activityText = `${tool} · ${text}`;
      break;
    }
    case 'status.working':
      if (isStaleRunId(event, executionRunId)) break;
      working = Boolean(event.active);
      if (!working) {
        activityText = '';
        blocks = blocks.map((block) =>
          block.kind === 'thinking' && block.text.trim() && !block.collapsed
            ? {kind: 'thinking', text: block.text, collapsed: true}
            : block,
        );
      }
      break;
    case 'plan.state':
      planStatus = typeof event.status === 'string' ? event.status : '';
      break;
    case 'confirm.request':
      if (typeof event.request_id === 'string' && typeof event.preview === 'string') {
        confirm = {
          requestId: event.request_id,
          preview: event.preview,
          allowApproveAll: Boolean(event.allow_approve_all),
        };
      }
      break;
    case 'confirm.done':
      if (confirm && event.request_id === confirm.requestId) confirm = undefined;
      break;
    case 'transcript.clear':
      blocks = [];
      activeTool = undefined;
      activeToolStartedAt = undefined;
      activityText = '';
      confirm = undefined;
      turns = 0;
      turnIndex = 0;
      assistantBuffer = '';
      result = undefined;
      working = false;
      executionRunId = undefined;
      executionState = 'idle';
      executionSequence = 0;
      break;
  }
  return {
    state: {
      greet,
      greetSub,
      model,
      root,
      mascotLines,
      mascotLabel,
      models,
      blocks,
      working,
      turnIndex,
      assistantBuffer,
      activeTool,
      activeToolStartedAt,
      planStatus,
      activityText,
      result,
      confirm,
      executionRunId,
      executionState,
      executionSequence,
    },
    turnCount: turns,
  };
}

export function reduceEvents(events: TerminalEvent[], initial: Partial<TerminalUiState> = {}): TerminalUiState {
  let state = createInitialState(initial);
  let turnCount = 0;
  for (const event of events) {
    const next = reduceState(state, event, turnCount);
    state = next.state;
    turnCount = next.turnCount;
  }
  return state;
}

export function parseTerminalEvent(line: string): TerminalEvent | null {
  const trimmed = line.trim();
  if (!trimmed) return null;
  try {
    const value = JSON.parse(trimmed) as unknown;
    return value && typeof value === 'object' && typeof (value as {type?: unknown}).type === 'string'
      ? (value as TerminalEvent)
      : null;
  } catch {
    return null;
  }
}

export function parseTerminalJsonl(text: string): TerminalEvent[] {
  return text.split('\n').map(parseTerminalEvent).filter((event): event is TerminalEvent => event !== null);
}

export function createEventReducer(initial: Partial<TerminalUiState> = {}) {
  let state = createInitialState(initial);
  let turnCount = 0;
  return {
    getState: () => state,
    reduce(event: TerminalEvent) {
      const next = reduceState(state, event, turnCount);
      state = next.state;
      turnCount = next.turnCount;
      return state.blocks;
    },
    reduceLine(line: string) {
      const event = parseTerminalEvent(line);
      return event ? this.reduce(event) : null;
    },
  };
}

export type EventReducer = ReturnType<typeof createEventReducer>;
