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

type InkPipeAppProps = {
  /** Python agent events (preferred — keeps process.stdin as the interactive TTY). */
  eventPort?: number;
  /** Legacy: JSONL events on stdin pipe (stdin cannot be used for keyboard). */
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
      socket.on('error', () => {
        // Keep Ink alive; Python owns the event server lifecycle.
      });
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
  const [ui, setUi] = useState<TerminalUiState>(() => createInitialState());
  const [inputText, setInputText] = useState('');
  const [scrollUpRows, setScrollUpRows] = useState(0);
  const confirmRef = useRef<TerminalUiState['confirm'] | undefined>(undefined);
  const welcomeCompact = ui.blocks.length > 0;
  const transcriptRows = transcriptRowBudget(rows, welcomeCompact);
  const maxScrollUp = useMemo(
    () => maxTranscriptScrollUp(ui.blocks, transcriptRows, columns),
    [ui.blocks, transcriptRows, columns],
  );
  const publishUi = useMemo(
    () => throttle((next: TerminalUiState) => setUi({...next}), 16),
    [],
  );

  const applyEvents = useMemo(
    () =>
      throttle((events: TerminalEvent[]) => {
        const reducer = reducerRef.current;
        for (const event of events) reducer.reduce(event);
        const next = reducer.getState();
        confirmRef.current = next.confirm;
        publishUi(next);
      }, 16),
    [publishUi],
  );

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
      publishUi(reducerRef.current.getState());
    },
    {isActive: inputActive},
  );

  useEffect(
    () => () => {
      publishUi.cancel();
      applyEvents.cancel();
    },
    [publishUi, applyEvents],
  );

  return (
    <Repl
      height={rows}
      columns={columns}
      greet={ui.greet}
      greetSub={ui.greetSub}
      model={ui.model}
      root={ui.root}
      mascotLines={ui.mascotLines}
      mascotLabel={ui.mascotLabel}
      blocks={ui.blocks}
      activeTool={ui.activeTool}
      activeToolStartedAt={ui.activeToolStartedAt}
      working={ui.working}
      confirm={ui.confirm}
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
