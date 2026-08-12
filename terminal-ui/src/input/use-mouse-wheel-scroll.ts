import {useEffect, useRef} from 'react';
import type {EventEmitter} from 'node:events';
import {useStderr, useStdin} from 'ink';
import {
  disableMouseWheelTracking,
  enableMouseWheelTracking,
  isMouseWheelEnabled,
  parseMouseWheelNotches,
  wheelLinesPerNotch,
} from './mouse-wheel.js';

type Options = {
  enabled: boolean;
  viewportRows: number;
  onWheelLines: (deltaLines: number) => void;
};

type StdinContext = {
  internal_eventEmitter?: EventEmitter;
};

function normalizeChunk(chunk: unknown): string {
  if (typeof chunk === 'string') return chunk;
  if (chunk instanceof Buffer) return chunk.toString('utf8');
  return String(chunk ?? '');
}

/** Capture mouse wheel in the Ink TTY and translate to transcript scroll deltas. */
export function useMouseWheelScroll({enabled, viewportRows, onWheelLines}: Options): void {
  const {internal_eventEmitter} = useStdin() as StdinContext;
  const {stderr} = useStderr();
  const onWheelLinesRef = useRef(onWheelLines);
  onWheelLinesRef.current = onWheelLines;

  useEffect(() => {
    if (!enabled || !internal_eventEmitter || !isMouseWheelEnabled()) return;

    const emitter = internal_eventEmitter;
    const write = (data: string) => {
      stderr.write(data);
    };
    enableMouseWheelTracking(write);

    const originalEmit = emitter.emit.bind(emitter);
    const patchedEmit: typeof emitter.emit = (event, ...args) => {
      if (event === 'input' && args.length > 0) {
        const {rest, notches} = parseMouseWheelNotches(normalizeChunk(args[0]));
        if (notches !== 0) {
          onWheelLinesRef.current(notches * wheelLinesPerNotch(viewportRows));
        }
        if (!rest) return false;
        return originalEmit(event, rest);
      }
      return originalEmit(event, ...args);
    };

    emitter.emit = patchedEmit;

    return () => {
      emitter.emit = originalEmit;
      disableMouseWheelTracking(write);
    };
  }, [enabled, internal_eventEmitter, stderr, viewportRows]);
}
