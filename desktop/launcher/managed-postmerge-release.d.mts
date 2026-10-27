export type ManagedPostmergeReleaseFile = {
  path: string;
  mode: '100644' | '100755';
  sha256: string;
  byteLength: number;
};

export type ManagedPostmergeReleaseDescriptor = {
  format: 1;
  mode: 'managed_postmerge_release_descriptor_v1';
  releaseId: string;
  manifestDigest: string;
  root: string;
  readyForLaunch: false;
};

/** Creates and verifies a new inert managed postmerge release below its build operation. */
export function sealManagedPostmergeRelease(input: {
  sandboxRoot: string;
  operationId: string;
}): ManagedPostmergeReleaseDescriptor;

/** Revalidates current source/build provenance and the exact release directory inventory. */
export function verifyManagedPostmergeRelease(input: {
  sandboxRoot: string;
  operationId: string;
  releaseId: string;
}): ManagedPostmergeReleaseDescriptor;
