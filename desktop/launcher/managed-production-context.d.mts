import type { ManagedRuntimeRegistration } from './managed-runtime-registration.mjs';

export interface ManagedProductionContextInput {
  stableRoot: string;
  sandboxRoot: string;
  installationId: string;
  operationId: string;
  releaseId: string;
}

export interface ManagedProductionSourceRoot {
  worktreeRoot: string;
  gitDir: string;
  commonDir: string;
  branch: string;
}

export interface ManagedProductionBinding {
  format: 1;
  mode: 'managed_production_binding_v1';
  installationId: string;
  namespace: {
    mode: 'managed_project_local_v1';
    root: string;
    digest: string;
    securityPolicy: 'logical_separation_only';
    credentialsPolicy: 'forbidden';
  };
  source: {
    operationId: string;
    planDigest: string;
    receiptDigest: string;
    snapshotDigest: string;
    mergedHead: string;
    mergedTree: string;
    roots: { stable: ManagedProductionSourceRoot; sandbox: ManagedProductionSourceRoot };
  };
  release: {
    releaseId: string;
    manifestDigest: string;
    root: string;
    buildReceiptDigest: string;
    toolchainDigest: string;
    buildPolicy: 'configless_snapshot_v2';
  };
  paths: {
    launcher: string;
    state: string;
    hostState: string;
    hostProfile: string;
    hostWorkspace: string;
    data: string;
    code: string;
  };
  data: { storeId: string; schema: 'managed_empty_store_v1'; revision: 0; policy: 'empty_no_migration' };
  access: 'read_only';
  runtimeRegistration: null;
  writerLease: null;
  readyForLaunch: false;
  bindingDigest: string;
}

export interface ManagedProductionContextDescriptor {
  format: 1;
  mode: 'managed_production_context_descriptor_v1';
  installationId: string;
  operationId: string;
  releaseId: string;
  root: string;
  bindingDigest: string;
  readyForLaunch: false;
}

export function prepareManagedProductionContext(input: ManagedProductionContextInput): Promise<ManagedProductionContextDescriptor>;
export function readManagedProductionContext(input: ManagedProductionContextInput): Promise<{
  descriptor: ManagedProductionContextDescriptor;
  binding: ManagedProductionBinding;
  manifest: Record<string, unknown>;
  runtimeRegistered: boolean;
  runtimeDigest: string | null;
  runtimeRegistration: ManagedRuntimeRegistration | null;
}>;

/** Strictly validate known operational Host profile/store records after initialization. */
export function readManagedOperationalProductionContext(input: ManagedProductionContextInput): Promise<{
  descriptor: ManagedProductionContextDescriptor;
  binding: ManagedProductionBinding;
  manifest: Record<string, unknown>;
  runtimeRegistered: true;
  runtimeDigest: string;
  runtimeRegistration: ManagedRuntimeRegistration;
  profileIntent: ManagedHostProfileIntent;
}>;

export interface ManagedHostProfileIntent {
  format: 1;
  mode: 'managed_host_profile_intent_v1';
  installationId: string;
  releaseId: string;
  bindingDigest: string;
  runtimeDigest: string;
  hostNonce: string;
  profileRoot: string;
  access: 'read_only' | 'fenced_application';
  intentDigest: string;
}

export function prepareManagedHostProfileIntent(
  input: ManagedProductionContextInput,
  request: { access: 'read_only' | 'fenced_application'; hostNonce: string },
): Promise<{
  descriptor: ManagedProductionContextDescriptor;
  binding: ManagedProductionBinding;
  manifest: Record<string, unknown>;
  runtimeRegistered: true;
  runtimeDigest: string;
  runtimeRegistration: ManagedRuntimeRegistration;
  profileIntent: ManagedHostProfileIntent;
}>;

/** Synchronous Host-only form. Completes verification and profile-intent publication before returning. */
export function prepareManagedHostProfileIntentSync(
  input: ManagedProductionContextInput,
  request: { access: 'read_only' | 'fenced_application'; hostNonce: string },
): {
  descriptor: ManagedProductionContextDescriptor;
  binding: ManagedProductionBinding;
  manifest: Record<string, unknown>;
  runtimeRegistered: true;
  runtimeDigest: string;
  runtimeRegistration: ManagedRuntimeRegistration;
  profileIntent: ManagedHostProfileIntent;
};
