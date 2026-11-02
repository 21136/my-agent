export type ManagedAnalysisOperation = "identity" | "review" | "prepare";
export type ManagedReviewRoots = { stableRoot: string; sandboxRoot: string };
type WorkerInput = { codeRoot: string; roots: ManagedReviewRoots; isTrusted: () => boolean };
type Finished<T extends object> = { type: "finished" } & T;
export function runManagedSourceAnalysis(input: WorkerInput & { operation: "identity" }): Promise<Finished<{ identity: Record<string, any> }>>;
export function runManagedSourceAnalysis(input: WorkerInput & { operation: "review" }): Promise<Finished<{ report: Record<string, any> }>>;
export function runManagedSourceAnalysis(input: WorkerInput & { operation: "prepare"; checkpoint: unknown }): Promise<Finished<{ plan: Record<string, any> }>>;
