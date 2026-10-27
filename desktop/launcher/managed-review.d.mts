declare function inspectManagedReview(input: { stableRoot: string; sandboxRoot: string }): Promise<Record<string, unknown>>;
declare function revalidateManagedReview(packet: Record<string, unknown>): Promise<Record<string, unknown>>;
export { inspectManagedReview, revalidateManagedReview };
