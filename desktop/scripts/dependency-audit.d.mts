export type DependencyIssue = { code: string; package: string; message: string };
export type DependencyAudit = { ok: boolean; root: string; platform: string; arch: string; checked: number; issues: DependencyIssue[]; repairHint: string };
export function auditDependencies(directory?: string, options?: { platform?: string; arch?: string }): DependencyAudit;
export function formatAudit(result: DependencyAudit): string;
export function assertDependencies(directory?: string): DependencyAudit;
