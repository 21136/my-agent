import { ContractError, canonicalJson, parseJsonBytes } from "./contracts";

export const MAX_FRAME_BYTES = 65_536;
const MAX_PUSH_BYTES = 1_048_576;
const HEADER_BYTES = 4;

export function encodeFrame(value: unknown): Buffer {
  const body = Buffer.from(canonicalJson(value), "utf8");
  if (body.length === 0 || body.length > MAX_FRAME_BYTES) throw new ContractError("budget_exceeded", "frame payload exceeds 64 KiB");
  const header = Buffer.allocUnsafe(HEADER_BYTES);
  header.writeUInt32BE(body.length, 0);
  return Buffer.concat([header, body]);
}

export class FrameDecoder {
  private pending = Buffer.alloc(0);
  private finished = false;

  push(chunk: Buffer): unknown[] {
    if (this.finished) throw new ContractError("invalid_request", "frame decoder is already finished");
    if (!Buffer.isBuffer(chunk)) throw new ContractError("invalid_request", "frame chunk must be a Buffer");
    if (chunk.length > MAX_PUSH_BYTES) throw new ContractError("budget_exceeded", "single decoder input exceeds 1 MiB");
    const data = this.pending.length === 0 ? chunk : Buffer.concat([this.pending, chunk]);
    const values: unknown[] = [];
    let offset = 0;
    while (data.length - offset >= HEADER_BYTES) {
      const size = data.readUInt32BE(offset);
      if (size === 0) throw new ContractError("invalid_request", "empty frame is not allowed");
      if (size > MAX_FRAME_BYTES) throw new ContractError("budget_exceeded", "declared frame exceeds 64 KiB");
      if (data.length - offset - HEADER_BYTES < size) break;
      values.push(parseJsonBytes(data.subarray(offset + HEADER_BYTES, offset + HEADER_BYTES + size)));
      offset += HEADER_BYTES + size;
    }
    this.pending = Buffer.from(data.subarray(offset));
    if (this.pending.length > HEADER_BYTES + MAX_FRAME_BYTES) throw new ContractError("budget_exceeded", "decoder buffer exceeds one frame");
    return values;
  }

  finish(): void {
    this.finished = true;
    if (this.pending.length !== 0) throw new ContractError("invalid_request", "stream ended inside a frame");
  }
}
