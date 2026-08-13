import {useLiveStream} from './use-live-stream.js';

export type LiveReasoningController = ReturnType<typeof useLiveReasoning>;

/** High-frequency reasoning buffer — paints on rAF, flushes to reducer on commit. */
export function useLiveReasoning() {
  return useLiveStream('reasoning.delta');
}
