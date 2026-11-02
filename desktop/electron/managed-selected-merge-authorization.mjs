export const MAIN_AUTHORIZATION_TIMEOUT_MS = 30_000;

export function waitForMainAuthorization(signalBuffer, timeoutMs = MAIN_AUTHORIZATION_TIMEOUT_MS) {
  if (!(signalBuffer instanceof SharedArrayBuffer)
      || signalBuffer.byteLength !== Int32Array.BYTES_PER_ELEMENT
      || !Number.isSafeInteger(timeoutMs) || timeoutMs <= 0) return false;
  const signal = new Int32Array(signalBuffer);
  const waitResult = Atomics.wait(signal, 0, 0, timeoutMs);
  if (waitResult === "timed-out") return false;
  // `not-equal` is a valid race: the host may grant before this worker enters wait.
  return Atomics.load(signal, 0) === 1;
}
