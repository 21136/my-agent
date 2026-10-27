export type ManagedPostmergeSourceFile = {
  path: string;
  mode: '100644' | '100755';
  blobOid: string;
  sha256: string;
  byteLength: number;
};

export type ManagedPostmergeSourceRoot = {
  worktreeRoot: string;
  gitDir: string;
  commonDir: string;
  branch: string;
};

/** Source provenance only; this snapshot does not verify a build or authorize launch. */
export type ManagedPostmergeSourceSnapshot = {
  format: 1;
  mode: 'managed_postmerge_source_snapshot_v1';
  status: 'source_merge_verified';
  planDigest: string;
  receiptDigest: string;
  roots: { stable: ManagedPostmergeSourceRoot; sandbox: ManagedPostmergeSourceRoot };
  mergedHead: string;
  mergedTree: string;
  files: ManagedPostmergeSourceFile[];
  excludedProtectedPaths: string[];
  snapshotDigest: string;
  readyForLaunch: false;
  buildVerified: false;
};

/** Reads verified v2 selected-merge evidence and the committed postmerge tree without writing Git objects or files. */
export function captureManagedPostmergeSource(input: {
  stableRoot: string;
  sandboxRoot: string;
  plan: unknown;
  receipt: unknown;
  gitExecutable: string;
}): ManagedPostmergeSourceSnapshot;
/** Host-only committed blob bytes, revalidated against the full source metadata gate. */
export function readManagedPostmergeSourcePayload(input: Parameters<typeof captureManagedPostmergeSource>[0]): {
  snapshot: ManagedPostmergeSourceSnapshot;
  files: {path:string;mode:'100644'|'100755';bytes:Buffer}[];
};
