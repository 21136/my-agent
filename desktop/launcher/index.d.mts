export type ReleaseDescriptor = {
    release_id: string;
    root: string;
    manifest_digest: string;
};
export type RuntimeManifest = {
    root: string;
    entry: string;
    scope: 'file' | 'tree';
    files: {
        path: string;
        sha256: string;
        byte_length: number;
    }[];
    directories: string[];
};
export type HostManifest = {
    format: 1;
    scope: 'launcher_fixture_v1';
    protocol: 1;
    data_layout: 1;
    files: {
        path: string;
        sha256: string;
        byte_length: number;
    }[];
    directories: string[];
    runtimes: {
        node: RuntimeManifest;
        electron: RuntimeManifest;
        python: RuntimeManifest;
        python_base:RuntimeManifest;
    };
    provenance: {
        kind: string;
        source_snapshot: string;
    };
};
export type FixtureBinding = {
    format: 1;
    mode: 'launcher_fixture_v1';
    installation_id: string;
    binding_revision: number;
    run_root: string;
    launcher_root: string;
    state_root: string;
    instance_root: string;
    stable_source_root: string;
    sandbox_source_root: string;
    git_common_dir: string;
    stable_branch: string;
    sandbox_branch: 'experiment/sandbox';
    release: ReleaseDescriptor;
};
export function bindingDigest(binding: FixtureBinding): string;
export function validateFixtureBinding(binding: unknown): {
    binding: FixtureBinding;
    digest: string;
    manifest: HostManifest;
};
export function readFixtureBinding(file: string): ReturnType<typeof validateFixtureBinding>;
export function cleanHostEnvironment(env?: NodeJS.ProcessEnv): NodeJS.ProcessEnv;
export function verifyHostRelease(descriptor: ReleaseDescriptor): HostManifest;
export function canonical(value: unknown): string;
export function assertRecord(value: unknown, keys: string[], label?: string): Record<string, unknown>;
export const HASH: RegExp;
export const UUID: RegExp;
export function readJson(file: string, maxBytes?: number): unknown;
export function regularFile(value: string, label?: string): string;
export function realDirectory(value: string, label?: string): string;
export function samePath(a: string, b: string): boolean;
export function sha(bytes: Buffer | string): string;
export type AdoptionJournal = {
    assertIdle(): void;
    begin?(status: {
        stableHead: string;
        sandboxHead: string;
        snapshotId: string;
        repoRoot: string;
        sandboxRoot: string;
    }): void;
    beforeWrite(event: {
        step: string;
        cwd: string;
    }): void;
    afterWrite(event: {
        step: string;
        cwd: string;
        code: number;
    }): void;
    backup(sha: string): void;
    finish(result: {
        ok: boolean;
        merged?: boolean;
        synced?: boolean;
        recoveryStash?: string;
    }): void;
};
export function createAdoptionJournal(stateRoot: string, identity: {
    installation_id: string;
    release_id: string;
    binding_digest: string;
}): AdoptionJournal;
