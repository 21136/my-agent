import { read } from "node:fs";

export const MAX_CONTROL_FRAME_BYTES = 16 * 1024;
export const MAX_PENDING_CONTROL_FRAMES = 64;
const READ_CHUNK_BYTES = 16 * 1024;

export class ManagedControlLineBuffer {
  private pending = Buffer.alloc(0);
  private droppingOversizedLine = false;

  get bufferedBytes(): number { return this.pending.length; }

  push(chunk: Uint8Array): string[] {
    const input = Buffer.concat([this.pending, Buffer.from(chunk)]);
    this.pending = Buffer.alloc(0);
    const lines: string[] = [];
    let offset = 0;
    while (offset < input.length) {
      const newline = input.indexOf(10, offset);
      if (newline < 0) {
        const remainder = input.subarray(offset);
        if (!this.droppingOversizedLine && remainder.length > MAX_CONTROL_FRAME_BYTES) this.droppingOversizedLine = true;
        else if (!this.droppingOversizedLine) this.pending = Buffer.from(remainder);
        break;
      }
      const frame = input.subarray(offset, newline);
      if (this.droppingOversizedLine) this.droppingOversizedLine = false;
      else if (frame.length <= MAX_CONTROL_FRAME_BYTES) {
        if (lines.length >= MAX_PENDING_CONTROL_FRAMES) throw new Error("managed control frame queue overflow");
        lines.push(frame.toString("utf8"));
      }
      offset = newline + 1;
    }
    return lines;
  }

  finish(): void {
    this.pending = Buffer.alloc(0);
    this.droppingOversizedLine = false;
  }
}

export interface ManagedControlInputCallbacks {
  onLine(line: string): Promise<void> | void;
  onEnd(): void;
  onFailure(): void;
}

export function startManagedControlInput(fd: number, callbacks: ManagedControlInputCallbacks): { stop(): void } {
  const lines = new ManagedControlLineBuffer();
  const queued: string[] = [];
  const readBuffer = Buffer.alloc(READ_CHUNK_BYTES);
  let stopped = false;
  let reading = false;
  let inputEnded = false;
  let endReported = false;
  let processing = false;

  const stop = () => {
    stopped = true;
    queued.length = 0;
    lines.finish();
  };
  const fail = () => {
    if (stopped) return;
    stop();
    try { callbacks.onFailure(); } catch { /* fail closed */ }
  };
  const reportEndIfDrained = () => {
    if (stopped || !inputEnded || processing || queued.length || endReported) return;
    endReported = true;
    stopped = true;
    lines.finish();
    try { callbacks.onEnd(); } catch { /* fail closed */ }
  };

  let readNext: () => void;
  let processNext: () => void;
  processNext = () => {
    if (stopped || processing) return;
    const line = queued.shift();
    if (line === undefined) { reportEndIfDrained(); if (!stopped && !inputEnded) readNext(); return; }
    processing = true;
    Promise.resolve().then(() => callbacks.onLine(line)).then(() => {
      processing = false;
      if (queued.length) processNext();
      else { reportEndIfDrained(); if (!stopped && !inputEnded) readNext(); }
    }, fail);
  };
  readNext = () => {
    if (stopped || inputEnded || reading || queued.length) return;
    reading = true;
    try {
      read(fd, readBuffer, 0, readBuffer.length, null, (error, bytesRead) => {
        reading = false;
        if (stopped) return;
        if (error) { fail(); return; }
        if (bytesRead === 0) {
          inputEnded = true;
          lines.finish();
          processNext();
          return;
        }
        try {
          queued.push(...lines.push(readBuffer.subarray(0, bytesRead)));
          if (queued.length > MAX_PENDING_CONTROL_FRAMES) { fail(); return; }
        } catch { fail(); return; }
        if (queued.length) processNext();
        else readNext();
      });
    } catch { reading = false; fail(); }
  };

  readNext();
  return { stop };
}
