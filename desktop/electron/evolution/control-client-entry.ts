import { ContractError, canonicalJson, parseControlRequest, type ControlRequest } from "./contracts";
import { verifyControlRelease, type ControlRelease } from "./control-release";
import {
  parseTrustedHostBindingRequest,
  requestControl,
  requestTrustedHostBinding,
  type ClientBootstrap,
  type TrustedHostBindingRequest,
  parseTrustedHostAuthorizationRequest,
  requestTrustedHostAuthorization,
  type TrustedHostAuthorizationRequest,
  parseTrustedHostResolveStopRequest,
  requestTrustedHostResolveStop,
  type TrustedHostResolveStopRequest,
  parseT9LateReadyRequest,
  requestT9LateReady,
  type T9LateReadyRequest,
} from "./transport";

const MAX_HELPER_INPUT = 131_072;
type ClientRequest = ControlRequest | TrustedHostBindingRequest | TrustedHostAuthorizationRequest | TrustedHostResolveStopRequest | T9LateReadyRequest;
type HelperInput = { bootstrap: ClientBootstrap; request: ClientRequest; timeout_ms: number };

function fail(message: string): never {
  throw new ContractError("invalid_request", message);
}

export function validateControlClientInput(value: unknown): HelperInput {
  if (value === null || typeof value !== "object" || Array.isArray(value)) fail("helper input must be an object");
  const input = value as Record<string, unknown>;
  const inputKeys = ["bootstrap", "request", "timeout_ms"];
  if (Object.keys(input).length !== inputKeys.length || inputKeys.some((key) => !Object.hasOwn(input, key))) fail("helper input fields are invalid");
  if (input.bootstrap === null || typeof input.bootstrap !== "object" || Array.isArray(input.bootstrap)) fail("bootstrap must be an object");
  const raw = input.bootstrap as Record<string, unknown>;
  const bootstrapKeys = ["pipe_name", "host_instance_id", "host_token", "control_release"];
  if (Object.keys(raw).length !== bootstrapKeys.length || bootstrapKeys.some((key) => !Object.hasOwn(raw, key))) fail("bootstrap fields are invalid");
  if (!Number.isSafeInteger(input.timeout_ms) || Number(input.timeout_ms) < 1 || Number(input.timeout_ms) > 600_000) fail("timeout_ms is outside the supported range");
  const requestType = input.request !== null && typeof input.request === "object" && !Array.isArray(input.request) ? (input.request as Record<string, unknown>).type : null;
  const request = requestType === "host.bind" ? parseTrustedHostBindingRequest(input.request) : requestType === "host.authorize" ? parseTrustedHostAuthorizationRequest(input.request) : requestType === "host.resolve-stop" ? parseTrustedHostResolveStopRequest(input.request) : requestType === "host.t9-late-ready" ? parseT9LateReadyRequest(input.request) : parseControlRequest(input.request);
  return { bootstrap: raw as unknown as ClientBootstrap, request, timeout_ms: Number(input.timeout_ms) };
}

export async function runControlClientEntry(inputValue: unknown): Promise<unknown> {
  const input = validateControlClientInput(inputValue);
  await verifyControlRelease(input.bootstrap.control_release as ControlRelease);
  if ("type" in input.request && input.request.type === "host.bind") {
    return await requestTrustedHostBinding({ bootstrap: input.bootstrap, request: input.request, timeoutMs: input.timeout_ms });
  }
  if ("type" in input.request && input.request.type === "host.authorize") {
    return await requestTrustedHostAuthorization({ bootstrap: input.bootstrap, request: input.request, timeoutMs: input.timeout_ms });
  }
  if ("type" in input.request && input.request.type === "host.t9-late-ready") {
    return await requestT9LateReady({ bootstrap: input.bootstrap, request: input.request, timeoutMs: input.timeout_ms });
  }
  if ("type" in input.request && input.request.type === "host.resolve-stop") {
    return await requestTrustedHostResolveStop({ bootstrap: input.bootstrap, request: input.request, timeoutMs: input.timeout_ms });
  }
  return await requestControl({ bootstrap: input.bootstrap, request: input.request as ControlRequest, timeoutMs: input.timeout_ms });
}

async function readStdin(): Promise<Buffer> {
  const chunks: Buffer[] = [];
  let size = 0;
  return await new Promise((resolve, reject) => {
    process.stdin.on("data", (chunk) => {
      const bytes = Buffer.from(chunk);
      size += bytes.length;
      if (size > MAX_HELPER_INPUT) {
        reject(new ContractError("budget_exceeded", "control helper input exceeds its byte limit"));
        process.stdin.destroy();
        return;
      }
      chunks.push(bytes);
    });
    process.stdin.once("error", reject);
    process.stdin.once("end", () => resolve(Buffer.concat(chunks, size)));
  });
}

async function main(): Promise<void> {
  const bytes = await readStdin();
  if (bytes.length === 0 || bytes.length > MAX_HELPER_INPUT) fail("control helper input is empty or too large");
  let value: unknown;
  try { value = JSON.parse(bytes.toString("utf8")); }
  catch { fail("control helper input is invalid JSON"); }
  const result = await runControlClientEntry(value);
  process.stdout.write(`${canonicalJson(result)}\n`);
}

if (require.main === module) {
  void main().catch(() => {
    process.stderr.write("control client unavailable\n");
    process.exitCode = 1;
  });
}

