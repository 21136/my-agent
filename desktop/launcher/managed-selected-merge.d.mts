export type ManagedSelectedMergeRequest = { format: 1; mode: 'managed_selected_merge_request_v1'; checkpointDigest: string };
export type ManagedSelectedMergeBlocker = { code: string; paths: string[]; disposition: 'preserve' | 'exclude' | 'review_required' };
export type ManagedSelectedMergeRootIdentity = { worktreeRoot: string; gitDir: string; commonDir: string; branch: string; head: string; rawIndexSha256: string };
export type ManagedSelectedMergeFile = {
  path: string; action: 'add' | 'replace'; rawSha256: string; mode: '100644' | '100755';
  expectedBaseOid: string | null; beforeSha256: string | null; expectedBaseMode: '100644' | '100755' | null;
};
export type ManagedSelectedMergePlan = {
  format: 1; mode: 'managed_selected_merge_plan_v1'; planDigest: string; checkpointDigest: string;
  stable: ManagedSelectedMergeRootIdentity; sandbox: ManagedSelectedMergeRootIdentity;
  selectedPaths: string[]; files: ManagedSelectedMergeFile[]; preservationDigest: string; gitMetadataDigest: string; temporaryIndexUpperBoundBytes: number;
  expectedMergedTree: string; expectedCommit: string; sourceHistoryMerged: false;
  stablePreservation?: { mode: 'managed_stable_working_preservation_v1'; rawBytes: number; expectedTree: string; expectedCommit: string;
    files: { path: string; sha256: string; oid: string; mode: '100644' | '100755'; bytesBase64: string }[] };
  preservationBasis: { stable: { sources: unknown[]; index: unknown[]; tree: unknown[]; stash: { refOid: string | null; reflogOids: string[][] } }; sandbox: { sources: unknown[]; index: unknown[]; tree: unknown[]; stash: { refOid: string | null; reflogOids: string[][] } } };
  blockers: ManagedSelectedMergeBlocker[]; execution: false; readyForLaunch: false;
};
export type ManagedSelectedMergeSuccessReceipt = {
  format: 1; mode: 'managed_selected_merge_receipt_v1'; status: 'source_merge_verified';
  planDigest: string; checkpointDigest: string; selectedPaths: string[]; stableHeadBefore: string;
  stableHeadAfter: string; mergedTree: string; sourceMergeExecuted: true;
  sourceHistoryMerged: false; readyForLaunch: false; receiptDigest: string;
};
export type ManagedSelectedMergeMaintenanceReceipt = {
  format: 1; mode: 'managed_selected_merge_receipt_v1'; status: 'maintenance_required';
  planDigest: string; checkpointDigest: string; maintenanceRequired: true; errorCode: string;
  intentPath: string; execution: boolean; readyForLaunch: false;
};
export type ManagedSelectedMergeReceipt = ManagedSelectedMergeSuccessReceipt;
export function validateManagedSelectedMergeRequest(value: unknown): ManagedSelectedMergeRequest;
export function prepareManagedSelectedMerge(input: { stableRoot: string; sandboxRoot: string; checkpoint: unknown; gitExecutable: string; preserveStableWorkingChanges?: boolean }): ManagedSelectedMergePlan;
export function inspectManagedSelectedMergeSourceIdentity(input: {stableRoot:string;sandboxRoot:string;gitExecutable:string}): {stable:ManagedSelectedMergeRootIdentity;sandbox:ManagedSelectedMergeRootIdentity;gitMetadataDigest:string};
export function executeManagedSelectedMerge(input: { plan: ManagedSelectedMergePlan; checkpoint: unknown; stableRoot: string; sandboxRoot: string; gitExecutable: string; directory?: string; assertTrusted: () => boolean }): ManagedSelectedMergeSuccessReceipt;
