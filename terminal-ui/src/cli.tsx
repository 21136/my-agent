#!/usr/bin/env node
import './bootstrap.js';
import {enableTrueColor} from './lib/truecolor.js';
enableTrueColor(process.stderr);

import React, {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {render, useInput, useStdout} from 'ink';
import net from 'node:net';
import {readFileSync} from 'node:fs';
import {Repl} from './repl.js';
import {
  createEventReducer,
  createInitialState,
  parseTerminalEvent,
  parseTerminalJsonl,
  type TerminalEvent,
  type TerminalUiState,
} from './reduce/events.js';
import {throttle} from './perf/throttle.js';
import {reduceTerminalInput} from './input.js';
import {
  maxTranscriptScrollUp,
  scrollTranscriptRows,
  transcriptRowBudget,
} from './perf/virtual-list.js';
import {useMouseWheelScroll} from './input/use-mouse-wheel-scroll.js';
import {useLiveReasoning} from './hooks/use-live-reasoning.js';
import {useLiveStream} from './hooks/use-live-stream.js';
import type {TerminalBlock} from './types.js';
import type {TerminalChrome, TerminalSession} from './repl/TerminalLayout.js';

const STRUCTURAL_EVENT_TYPES = new Set([
  'turn.start',
  'user.message',
  'notice',
  'assistant.done',
  'transcript.clear',
]);

const CHROME_EVENT_TYPES = new Set([
  'tool.active',
  'tool.clear',
  'tool.progress',
  'activity.update',
  'status.working',
  'plan.state',
  'confirm.request',
  'confirm.done',
]);

const SESSION_EVENT_TYPES = new Set(['session.init']);

function loadFixture(path: string): TerminalEvent[] {
  return parseTerminalJsonl(readFileSync(path, 'utf8'));
}

function reduceAll(events: TerminalEvent[]): TerminalUiState {
  const reducer = createEventReducer();
  for (const event of events) reducer.reduce(event);
  return reducer.getState();
}

function send(message: Record<string, unknown>) {
  process.stdout.write(`${JSON.stringify(message)}\n`);
}

function sessionFromState(state: TerminalUiState): TerminalSession {
  return {
    greet: state.greet,
    greetSub: state.greetSub,
    model: state.model,
    root: state.root,
    mascotLines: state.mascotLines,
    mascotLabel: state.mascotLabel,
  };
}

function chromeFromState(state: TerminalUiState): TerminalChrome {
  return {
    working: state.working,
    activeTool: state.activeTool,
    activeToolStartedAt: state.activeToolStartedAt,
    planStatus: state.planStatus,
    confirm: state.confirm,
  };
}

type InkPipeAppProps = {
  eventPort?: number;
  eventsOnStdin?: boolean;
};

function useEventStream(
  eventPort: number | undefined,
  eventsOnStdin: boolean,
  onEvents: (events: TerminalEvent[]) => void,
) {
  const bufferRef = useRef('');

  const consume = (chunk: string) => {
    bufferRef.current += chunk;
    const parts = bufferRef.current.split('\n');
    bufferRef.current = parts.pop() ?? '';
    const parsed: TerminalEvent[] = [];
    for (const line of parts) {
      const event = parseTerminalEvent(line);
      if (event) parsed.push(event);
    }
    if (parsed.length > 0) onEvents(parsed);
  };

  useEffect(() => {
    if (eventPort !== undefined) {
      const socket = net.createConnection({host: '127.0.0.1', port: eventPort});
      socket.setEncoding('utf8');
      socket.on('data', consume);
      socket.on('error', () => {});
      return () => {
        socket.removeListener('data', consume);
        socket.destroy();
      };
    }
    if (!eventsOnStdin) return;
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', consume);
    return () => {
      process.stdin.off('data', consume);
    };
  }, [eventPort, eventsOnStdin, onEvents]);
}

function InkPipeApp({eventPort, eventsOnStdin = false}: InkPipeAppProps) {
  const {stdout} = useStdout();
  const rows = Math.max(process.stderr.rows ?? 0, stdout.rows ?? 0, 24);
  const columns = Math.max(process.stderr.columns ?? 0, stdout.columns ?? 0, 80);
  const reducerRef = useRef(createEventReducer());
  const confirmRef = useRef<TerminalUiState['confirm'] | undefined>(undefined);

  const [session, setSession] = useState<TerminalSession>(() => sessionFromState(createInitialState()));
  const [chrome, setChrome] = useState<TerminalChrome>(() => chromeFromState(createInitialState()));
  const [blocks, setBlocks] = useState<TerminalBlock[]>([]);
  const [inputText, setInputText] = useState('');
  const [scrollUpRows, setScrollUpRows] = useState(0);

  const {
    text: liveReasoningText,
    append: appendLiveReasoning,
    flushToReducer: flushLiveReasoning,
    reset: resetLiveReasoning,
  } = useLiveReasoning();
  const {
    text: liveAssistantText,
    append: appendLiveAssistant,
    flushToReducer: flushLiveAssistant,
    reset: resetLiveAssistant,
  } = useLiveStream('assistant.delta');

  const welcomeCompact = blocks.length > 0;
  const transcriptRows = transcriptRowBudget(rows, welcomeCompact);
  const maxScrollUp = useMemo(
    () => maxTranscriptScrollUp(blocks, transcriptRows, columns),
    [blocks, transcriptRows, columns],
  );

  const assistantLiveRef = useRef(false);

  const publishChrome = useMemo(
    () => throttle((next: TerminalChrome) => setChrome({...next}), 16),
    [],
  );

  const syncSession = useCallback((state: TerminalUiState) => {
    setSession(sessionFromState(state));
  }, []);

  const flushLiveStreams = useCallback(
    (reducer: ReturnType<typeof createEventReducer>) => {
      flushLiveReasoning(reducer);
      flushLiveAssistant(reducer);
    },
    [flushLiveAssistant, flushLiveReasoning],
  );

  const applyCommittedEvents = useCallback(
    (events: TerminalEvent[]) => {
      if (events.length === 0) return;
      const reducer = reducerRef.current;
      flushLiveStreams(reducer);
      let structural = false;
      let chromeChanged = false;
      let sessionChanged = false;

      for (const event of events) {
        if (event.type === 'turn.start') {
          resetLiveReasoning();
          resetLiveAssistant();
          assistantLiveRef.current = false;
        }
        reducer.reduce(event);
        if (STRUCTURAL_EVENT_TYPES.has(event.type)) structural = true;
        if (CHROME_EVENT_TYPES.has(event.type)) chromeChanged = true;
        if (SESSION_EVENT_TYPES.has(event.type)) sessionChanged = true;
      }

      const next = reducer.getState();
      confirmRef.current = next.confirm;
      if (sessionChanged) syncSession(next);
      if (structural) setBlocks([...next.blocks]);
      if (chromeChanged) publishChrome(chromeFromState(next));
      if (structural && !chromeChanged) {
        // Structural events like assistant.done also flip working state.
        publishChrome(chromeFromState(next));
      }
    },
    [
      flushLiveStreams,
      publishChrome,
      resetLiveAssistant,
      resetLiveReasoning,
      syncSession,
    ],
  );

  const applyEvents = useCallback(
    (events: TerminalEvent[]) => {
      const committed: TerminalEvent[] = [];
      for (const event of events) {
        if (event.type === 'reasoning.delta') {
          appendLiveReasoning(typeof event.text === 'string' ? event.text : '');
          continue;
        }
        if (event.type === 'assistant.delta') {
          const text = typeof event.text === 'string' ? event.text : '';
          const reducer = reducerRef.current;
          if (!assistantLiveRef.current) {
            assistantLiveRef.current = true;
            flushLiveReasoning(reducer);
            reducer.reduce({type: 'assistant.delta', text: ''});
            setBlocks([...reducer.getState().blocks]);
          }
          appendLiveAssistant(text);
          continue;
        }
        committed.push(event);
      }
      if (committed.length > 0) applyCommittedEvents(committed);
    },
    [appendLiveAssistant, appendLiveReasoning, applyCommittedEvents, flushLiveReasoning],
  );

  useEffect(() => {
    return () => {
      const reducer = reducerRef.current;
      flushLiveStreams(reducer);
      setBlocks([...reducer.getState().blocks]);
    };
  }, [flushLiveStreams]);

  useEventStream(eventPort, eventsOnStdin, applyEvents);

  useEffect(() => {
    setScrollUpRows((current) => Math.min(current, maxScrollUp));
  }, [maxScrollUp]);

  const inputActive = eventPort !== undefined ? Boolean(stdout.isTTY) : false;
  const scrollStep = Math.max(3, Math.floor(transcriptRows / 2));
  const applyScrollLines = useCallback(
    (deltaLines: number) => {
      setScrollUpRows((current) => scrollTranscriptRows(current, deltaLines, maxScrollUp));
    },
    [maxScrollUp],
  );

  useMouseWheelScroll({
    enabled: inputActive,
    viewportRows: transcriptRows,
    onWheelLines: applyScrollLines,
  });

  useInput(
    (input, key) => {
      if (!confirmRef.current) {
        if (key.pageUp) {
          applyScrollLines(scrollStep);
          return;
        }
        if (key.pageDown) {
          applyScrollLines(-scrollStep);
          return;
        }
        if (!inputText) {
          if (key.upArrow) {
            applyScrollLines(3);
            return;
          }
          if (key.downArrow) {
            applyScrollLines(-3);
            return;
          }
        }
      }

      const confirm = confirmRef.current;
      const result = reduceTerminalInput(
        {text: inputText},
        input,
        key,
        confirm ? {allowApproveAll: confirm.allowApproveAll} : undefined,
      );
      if (result.action.type === 'confirm' && confirm) {
        send({
          type: 'confirm.response',
          request_id: confirm.requestId,
          choice: result.action.choice,
        });
      } else if (result.action.type === 'submit') {
        send({type: 'input.line', text: result.action.text});
        setScrollUpRows(0);
      } else if (result.action.type === 'cancel') {
        send({type: 'turn.cancel'});
      }
      setInputText(result.action.type === 'submit' ? '' : result.state.text);
    },
    {isActive: inputActive},
  );

  useEffect(
    () => () => {
      publishChrome.cancel();
    },
    [publishChrome],
  );

  return (
    <Repl
      height={rows}
      columns={columns}
      greet={session.greet}
      greetSub={session.greetSub}
      model={session.model}
      root={session.root}
      mascotLines={session.mascotLines}
      mascotLabel={session.mascotLabel}
      blocks={blocks}
      liveReasoningText={liveReasoningText}
      liveAssistantText={liveAssistantText}
      activeTool={chrome.activeTool}
      activeToolStartedAt={chrome.activeToolStartedAt}
      planStatus={chrome.planStatus}
      working={chrome.working}
      confirm={chrome.confirm}
      input={inputText}
      scrollUpRows={scrollUpRows}
    />
  );
}

function renderInkApp(app: React.ReactElement) {
  enableTrueColor(process.stderr);
  render(app, {stdout: process.stderr, exitOnCtrlC: false});
}

const fixtureArg = process.argv[2];
const eventPortRaw = process.env.MY_AGENT_TERMINAL_EVENT_PORT;
const eventPort =
  eventPortRaw && /^\d+$/.test(eventPortRaw) ? Number(eventPortRaw) : undefined;
const legacyStdinPipe = !process.stdin.isTTY && !fixtureArg && eventPort === undefined;

if (fixtureArg) {
  const ui = reduceAll(loadFixture(fixtureArg));
  render(
    <Repl
      greet={ui.greet}
      model={ui.model}
      root={ui.root}
      blocks={ui.blocks}
      activeTool={ui.activeTool}
      activeToolStartedAt={ui.activeToolStartedAt}
      planStatus={ui.planStatus}
      working={ui.working}
    />,
  );
} else if (eventPort !== undefined) {
  renderInkApp(<InkPipeApp eventPort={eventPort} />);
} else if (legacyStdinPipe) {
  renderInkApp(<InkPipeApp eventsOnStdin />);
} else {
  render(<Repl />);
}
