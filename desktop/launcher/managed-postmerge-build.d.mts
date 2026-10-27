export type ManagedPostmergeBuildFile = {
  path: string;
  mode: '100644' | '100755';
  sha256: string;
  byteLength: number;
};

export type ManagedPostmergeBuildReceipt = {
  format: 1;
  mode: 'managed_postmerge_build_receipt_v1';
  operationId: string;
  planDigest: string;
  snapshotDigest: string;
  materializedReceiptDigest: string;
  toolchainDigest: string;
  buildPolicy: 'configless_snapshot_v2';
  outputs: ManagedPostmergeBuildFile[];
  inputsDigest: string;
  buildVerified: true;
  readyForLaunch: false;
  receiptDigest: string;
};

export type ManagedPostmergeBuildResult = {
  operationRoot: string;
  receipt: ManagedPostmergeBuildReceipt;
};

export const BUILD_POLICY: 'configless_snapshot_v2';

/** Configless, byte-bound build of one verified materialized postmerge operation. */
export function buildManagedPostmerge(input: {
  sandboxRoot: string;
  operationId: string;
  toolchainRoot: string;
}): Promise<ManagedPostmergeBuildResult>;
