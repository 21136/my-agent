export type ManagedSelectionRequest = {
  format: 1;
  mode: 'managed_source_selection_request_v1';
  reviewDigest: string;
  selectedPaths: string[];
};

export function validateManagedSelectionRequest(value: unknown): ManagedSelectionRequest;
export function createManagedSourceSelectionProposal(input: {
  review: unknown;
  request: unknown;
}): Record<string, unknown>;
