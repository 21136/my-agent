export type ManagedToolchainReadGuardReceipt = {
  mode: 'managed_toolchain_read_guard_v1';
  toolchainDigest: string;
  fileCount: number;
  runtimeFileCount: 1;
  directoryCount: number;
};

export type ManagedToolchainReadGuard = {
  /** Releases held Windows handles and requires a nonce-bound child acknowledgement. */
  release(): Promise<void>;
  receipt: ManagedToolchainReadGuardReceipt;
};

/**
 * Holds read-only Windows handles for every file in the verified toolchain
 * inventory, its package metadata, and all ancestor directories through the drive root.
 */
export function acquireManagedToolchainReadGuard(input: {
  toolchain: {
    format: 1;
    mode: 'managed_postmerge_toolchain_v1';
    root: string;
    packageJsonSha256: string;
    packageLockSha256: string;
    node: { executable: string; sha256: string; version: string };
    files: Array<{ path: string; mode: '100644' | '100755'; sha256: string; byteLength: number }>;
    toolchainDigest: string;
  };
}): Promise<ManagedToolchainReadGuard>;
