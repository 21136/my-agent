import type { ManagedProductionContextInput } from './managed-production-context.mjs';

export interface ManagedRuntimeInput {
  root: string;
  entry: string;
}

export interface ManagedRuntimeFileRecord {
  path: string;
  sha256: string;
  byteLength: number;
}

export interface ManagedRuntimeRecord {
  root: string;
  entry: string;
  policy: 'single_executable_v1' | 'complete_tree_v1';
  files: ManagedRuntimeFileRecord[];
  directories: string[];
}

export interface ManagedRuntimeRegistration {
  format: 1;
  mode: 'managed_runtime_registration_v1';
  installationId: string;
  releaseId: string;
  bindingDigest: string;
  runtimes: {
    node: ManagedRuntimeRecord;
    electron: ManagedRuntimeRecord;
    python: ManagedRuntimeRecord;
    pythonBase: ManagedRuntimeRecord;
  };
  readyForLaunch: false;
  runtimeDigest: string;
}

export interface ManagedRuntimeRegistrationDescriptor {
  format: 1;
  mode: 'managed_runtime_registration_descriptor_v1';
  installationId: string;
  releaseId: string;
  bindingDigest: string;
  runtimeDigest: string;
  root: string;
  readyForLaunch: false;
}

export function prepareManagedRuntimeRegistration(input: {
  context: ManagedProductionContextInput;
  runtimes: { node: ManagedRuntimeInput; electron: ManagedRuntimeInput; python: ManagedRuntimeInput; pythonBase: ManagedRuntimeInput };
}): Promise<{ descriptor: ManagedRuntimeRegistrationDescriptor; registration: ManagedRuntimeRegistration }>;

export function readManagedRuntimeRegistration(context: ManagedProductionContextInput): Promise<{
  descriptor: ManagedRuntimeRegistrationDescriptor;
  registration: ManagedRuntimeRegistration;
}>;

/** Validate only the exact launcher schemas and digest bindings; this does not verify current runtime bytes. */
export function validateManagedRuntimeRegistrationRecords(
  launcherRoot: string,
  expected: { installationId: string; releaseId: string; bindingDigest: string },
): ManagedRuntimeRegistration;

/** @internal Host-only read-only capture of the four explicitly supplied runtime roots; never executes them. */
export function captureManagedRuntimeInputs(runtimes: {
  node: ManagedRuntimeInput;
  electron: ManagedRuntimeInput;
  python: ManagedRuntimeInput;
  pythonBase: ManagedRuntimeInput;
}): ManagedRuntimeRegistration['runtimes'];

/** @internal Incrementally read and bound one directory without invoking a runtime. */
export function readManagedRuntimeDirectoryEntries(directory: string, maxEntries?: number): string[];
