import { randomBytes, randomUUID } from "node:crypto";
import * as path from "node:path";
import { encodeEvolutionSidecarBootstrap, SidecarBootstrapRegistry, type EvolutionSidecarBootstrap } from "./sidecar-bootstrap";
import type { ControlRelease } from "./control-release";
import { verifyControlRelease } from "./control-release";
import { canonicalJson, ContractError } from "./contracts";
import { createSupervisorRuntime, type SupervisorProcessConfig, type SupervisorRuntime, type SupervisorRuntimeFactories } from "./supervisor-process";
import { serveSupervisor } from "./supervisor-entry";
import type { EvolutionSupervisor } from "./supervisor";
import type { HostBootstrap } from "./transport";

export type EvolutionSupervisorHostConfig = Omit<SupervisorProcessConfig, "host_bootstrap"> & { control_release: ControlRelease; sidecar_root: string };
type HostIdentity = { pipe_name: string; host_instance_id: string; host_token: string };
type HostTransport = { close(): Promise<void> };
export type EvolutionSupervisorHostFactories = {
  verifyControlRelease: (release: ControlRelease) => Promise<void>;
  createRuntime: (config: SupervisorProcessConfig, factories?: Partial<SupervisorRuntimeFactories>) => Promise<SupervisorRuntime>;
  serveSupervisor: (input: { bootstrap: HostBootstrap; supervisor: EvolutionSupervisor }) => Promise<HostTransport>;
  createHostIdentity: () => HostIdentity;
};
export type EvolutionSupervisorHost = {
  getSidecarBootstrap(root: string): EvolutionSidecarBootstrap | null;
  close(): Promise<void>;
};
export type CreateEvolutionSupervisorHostOptions = {
  factories?: Partial<EvolutionSupervisorHostFactories>;
  runtimeFactories?: Partial<SupervisorRuntimeFactories>;
  bootstrapRegistry?: SidecarBootstrapRegistry;
};

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const TOKEN = /^[0-9a-f]{64}$/;
const PIPE_PREFIX = "\\\\.\\pipe\\";
const CONFIG_FIELDS = ["protocol", "sandbox_root", "sidecar_root", "run_root", "control_release", "scopes", "runtime_paths", "descriptors", "profile_fixtures", "descriptor_factories"];
function fail(message: string): never { throw new ContractError("invalid_request", message); }
function exactObject(value: unknown, fields: string[], label: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value) || Object.getPrototypeOf(value) !== Object.prototype && Object.getPrototypeOf(value) !== null) fail(`${label} must be a plain object`);
  const item = value as Record<string, unknown>;
  if (Object.keys(item).length !== fields.length || fields.some((field) => !Object.hasOwn(item, field))) fail(`${label} fields are missing or unknown`);
  return item;
}
function generatedIdentity(): HostIdentity {
  const hostInstanceId = randomUUID();
  return { pipe_name: `${PIPE_PREFIX}my-agent-evolution-${hostInstanceId}`, host_instance_id: hostInstanceId, host_token: randomBytes(32).toString("hex") };
}
function validateIdentity(value: unknown): HostIdentity {
  const item = exactObject(value, ["pipe_name", "host_instance_id", "host_token"], "host identity");
  if (typeof item.pipe_name !== "string" || !item.pipe_name.startsWith(PIPE_PREFIX) || item.pipe_name.length > 240 || item.pipe_name.slice(PIPE_PREFIX.length).includes("\\") || item.pipe_name.slice(PIPE_PREFIX.length).includes("/")) fail("host pipe name is invalid");
  if (typeof item.host_instance_id !== "string" || !UUID.test(item.host_instance_id) || typeof item.host_token !== "string" || !TOKEN.test(item.host_token)) fail("host identity or token is invalid");
  return { pipe_name: item.pipe_name, host_instance_id: item.host_instance_id, host_token: item.host_token };
}
function rootKey(value: string): string | null {
  if (typeof value !== "string" || !path.isAbsolute(value)) return null;
  const resolved = path.resolve(value);
  return process.platform === "win32" ? resolved.toLowerCase() : resolved;
}
function inside(root: string, target: string): boolean {
  const relative = path.relative(path.resolve(root), path.resolve(target));
  return relative !== "" && relative !== ".." && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative);
}
function samePath(left: string, right: string): boolean { return rootKey(left) !== null && rootKey(left) === rootKey(right); }
function samePathList(left: string[], right: string[]): boolean { return left.length === right.length && left.every((value, index) => samePath(value, right[index])); }
function runtimeMatches(runtime: SupervisorProcessConfig, requested: SupervisorProcessConfig, identity: HostIdentity, release: ControlRelease, scopeId: string): boolean {
  const activeScope = runtime.scopes[0];
  const requestedScope = requested.scopes[0];
  if (runtime.protocol !== requested.protocol || !samePath(runtime.sandbox_root, requested.sandbox_root) || !samePath(runtime.run_root, requested.run_root) || runtime.scopes.length !== 1 || !activeScope || !requestedScope) return false;
  const host = runtime.host_bootstrap;
  if (host.host_instance_id !== identity.host_instance_id || host.host_token !== identity.host_token || host.pipe_name !== identity.pipe_name || canonicalJson(host.control_release) !== canonicalJson(release) || !Array.isArray(host.allowed_scope_ids) || host.allowed_scope_ids.length !== 1 || host.allowed_scope_ids[0] !== scopeId) return false;
  if (activeScope.scope_id !== scopeId || !samePath(activeScope.source_root, requestedScope.source_root) || !samePath(activeScope.state_root, requestedScope.state_root) || canonicalJson(activeScope.owned_paths) !== canonicalJson(requestedScope.owned_paths) || !samePathList(activeScope.read_only_roots, requestedScope.read_only_roots)) return false;
  if (canonicalJson(runtime.descriptors) !== canonicalJson(requested.descriptors) || canonicalJson(runtime.profile_fixtures.map((item) => item.id)) !== canonicalJson(requested.profile_fixtures.map((item) => item.id)) || !samePathList(runtime.profile_fixtures.map((item) => item.root), requested.profile_fixtures.map((item) => item.root)) || canonicalJson(runtime.descriptor_factories ?? []) !== canonicalJson(requested.descriptor_factories ?? [])) return false;
  const activeRuntimeIds = Object.keys(runtime.runtime_paths).sort();
  const requestedRuntimeIds = Object.keys(requested.runtime_paths).sort();
  return canonicalJson(activeRuntimeIds) === canonicalJson(requestedRuntimeIds) && activeRuntimeIds.every((id) => samePath(runtime.runtime_paths[id], requested.runtime_paths[id]));
}
function closeRuntime(runtime: SupervisorRuntime, hostInstanceId: string): void {
  try { runtime.supervisor.disconnectHost(hostInstanceId); }
  finally { runtime.closeJournal(); }
}

export async function createEvolutionSupervisorHost(configValue: EvolutionSupervisorHostConfig, options: CreateEvolutionSupervisorHostOptions = {}): Promise<EvolutionSupervisorHost> {
  const hasDescriptorFactories = configValue !== null && typeof configValue === "object" && Object.hasOwn(configValue, "descriptor_factories");
  const config = exactObject(configValue, hasDescriptorFactories ? CONFIG_FIELDS : CONFIG_FIELDS.slice(0, -1), "evolution supervisor host config") as unknown as EvolutionSupervisorHostConfig;
  if (config.protocol !== "my-agent-evolution-supervisor/1.0" || typeof config.sandbox_root !== "string" || !path.isAbsolute(config.sandbox_root) || typeof config.sidecar_root !== "string" || !path.isAbsolute(config.sidecar_root) || typeof config.run_root !== "string" || !path.isAbsolute(config.run_root)) fail("host roots are invalid");
  if (!Array.isArray(config.scopes) || config.scopes.length !== 1) fail("sidecar bootstrap requires exactly one trusted workspace scope");
  const scope = config.scopes[0];
  if (!scope || typeof scope.scope_id !== "string" || !UUID.test(scope.scope_id) || !Array.isArray(scope.owned_paths)) fail("trusted workspace scope is invalid");
  if (rootKey(config.sidecar_root) !== rootKey(scope.source_root) || !inside(config.sandbox_root, config.sidecar_root)) fail("trusted sidecar root must match its configured source scope inside the sandbox");

  const factories = options.factories ?? {};
  const verifyRelease = factories.verifyControlRelease ?? verifyControlRelease;
  const createRuntime = factories.createRuntime ?? ((value: SupervisorProcessConfig, runtimeFactories?: Partial<SupervisorRuntimeFactories>) => createSupervisorRuntime(value, runtimeFactories));
  const serve = factories.serveSupervisor ?? serveSupervisor;
  const identity = validateIdentity((factories.createHostIdentity ?? generatedIdentity)());
  const stableRootKey = rootKey(config.sidecar_root);
  if (!stableRootKey) fail("stable sidecar root must be absolute");
  const hostBootstrap: HostBootstrap = {
    pipe_name: identity.pipe_name,
    host_instance_id: identity.host_instance_id,
    host_token: identity.host_token,
    control_release: structuredClone(config.control_release),
    allowed_scope_ids: [scope.scope_id],
  };
  const runtimeConfig: SupervisorProcessConfig = {
    protocol: config.protocol,
    sandbox_root: config.sandbox_root,
    run_root: config.run_root,
    host_bootstrap: hostBootstrap,
    scopes: structuredClone(config.scopes),
    runtime_paths: structuredClone(config.runtime_paths),
    descriptors: structuredClone(config.descriptors),
    profile_fixtures: structuredClone(config.profile_fixtures),
    ...(config.descriptor_factories ? { descriptor_factories: structuredClone(config.descriptor_factories) } : {}),
  };
  const requestedSidecarBootstrap: EvolutionSidecarBootstrap = {
    protocol: "my-agent-evolution-sidecar/1.0",
    control_release: structuredClone(config.control_release),
    pipe_name: identity.pipe_name,
    host_instance_id: identity.host_instance_id,
    host_token: identity.host_token,
    scope_id: scope.scope_id,
    owned_paths: scope.owned_paths.slice(),
  };
  encodeEvolutionSidecarBootstrap(requestedSidecarBootstrap);

  await verifyRelease(config.control_release);
  let runtime: SupervisorRuntime | null = null;
  let transport: HostTransport | null = null;
  let sidecarBootstrap: EvolutionSidecarBootstrap | null = null;
  const registry = options.bootstrapRegistry ?? new SidecarBootstrapRegistry();
  try {
    runtime = await createRuntime(runtimeConfig, options.runtimeFactories);
    const activeBootstrap = runtime.config.host_bootstrap;
    const activeScope = runtime.config.scopes[0];
    if (!runtimeMatches(runtime.config, runtimeConfig, identity, config.control_release, scope.scope_id) || !activeScope) fail("supervisor runtime changed the fixed release, host identity, or trusted scope");
    sidecarBootstrap = {
      protocol: "my-agent-evolution-sidecar/1.0",
      control_release: structuredClone(activeBootstrap.control_release),
      pipe_name: activeBootstrap.pipe_name,
      host_instance_id: activeBootstrap.host_instance_id,
      host_token: activeBootstrap.host_token,
      scope_id: activeScope.scope_id,
      owned_paths: activeScope.owned_paths.slice(),
    };
    encodeEvolutionSidecarBootstrap(sidecarBootstrap);
    transport = await serve({ bootstrap: activeBootstrap, supervisor: runtime.supervisor });
    registry.register(config.sidecar_root, sidecarBootstrap);
  } catch (error) {
    registry.clear(config.sidecar_root);
    if (transport) { try { await transport.close(); } catch { /* preserve the startup error */ } }
    if (runtime) { try { closeRuntime(runtime, identity.host_instance_id); } catch { /* preserve the startup error */ } }
    throw error;
  }

  const ownedRuntime = runtime;
  const ownedTransport = transport;
  let active: EvolutionSidecarBootstrap | null = sidecarBootstrap;
  let closed = false;
  return {
    getSidecarBootstrap(root: string): EvolutionSidecarBootstrap | null {
      if (closed || active === null || rootKey(root) !== stableRootKey) return null;
      return structuredClone(active);
    },
    async close(): Promise<void> {
      if (closed) return;
      closed = true;
      active = null;
      registry.clear(config.sidecar_root);
      let firstError: unknown = null;
      try { await ownedTransport.close(); } catch (error) { firstError = error; }
      try { closeRuntime(ownedRuntime, identity.host_instance_id); } catch (error) { firstError ??= error; }
      if (firstError) throw firstError;
    },
  };
}
