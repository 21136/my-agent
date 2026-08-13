import {useCallback, useEffect, useRef, useState} from 'react';
import type {EventReducer, TerminalEvent} from '../reduce/events.js';
import {createFrameScheduler} from '../perf/schedule-frame.js';

export type LiveStreamController = {
  text: string;
  append: (chunk: string) => void;
  flushToReducer: (reducer: EventReducer) => void;
  reset: () => void;
};

export function useLiveStream(
  eventType: Extract<TerminalEvent['type'], 'reasoning.delta' | 'assistant.delta'>,
): LiveStreamController {
  const bufferRef = useRef('');
  const [text, setText] = useState('');
  const frameRef = useRef(createFrameScheduler());

  const paint = useCallback(() => {
    setText(bufferRef.current);
  }, []);

  const reset = useCallback(() => {
    frameRef.current.cancel();
    bufferRef.current = '';
    setText('');
  }, []);

  const append = useCallback(
    (chunk: string) => {
      if (!chunk) return;
      bufferRef.current += chunk;
      frameRef.current.schedule(paint);
    },
    [paint],
  );

  const flushToReducer = useCallback(
    (reducer: EventReducer) => {
      frameRef.current.cancel();
      const pending = bufferRef.current;
      bufferRef.current = '';
      setText('');
      if (pending) reducer.reduce({type: eventType, text: pending});
    },
    [eventType],
  );

  useEffect(() => () => frameRef.current.cancel(), []);

  return {text, append, flushToReducer, reset};
}
