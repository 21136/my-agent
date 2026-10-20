export interface ProjectVerificationResult {
  type: "project.verify.done";
  ok: boolean;
  passed: boolean;
  exit_code?: number;
  command?: string;
  path?: string;
}

export interface SharedVerificationEvent {
  id: string;
  title: string;
  body: string;
  sourceId: string;
}

function stableHash(value: string): string {
  const mask = 0xffff_ffff_ffff_ffffn;
  let hash = 0xcbf29ce484222325n;
  for (let i = 0; i < value.length; i++) {
    hash ^= BigInt(value.charCodeAt(i));
    hash = (hash * 0x100000001b3n) & mask;
  }
  return hash.toString(16).padStart(16, "0");
}

/** Convert only an explicit, successful project verification result into a quiet shared-world record. */
export function verifiedProjectEventToSharedEvent(
  event: ProjectVerificationResult,

): SharedVerificationEvent | null {

  if (event?.type !== "project.verify.done" || event.ok !== true || event.passed !== true) return null;

  const identity = [event.command ?? "", event.path ?? "", String(event.exit_code ?? "")].join("\u0000");
  const fingerprint = stableHash(identity);
  return {
    id: `project-verify-${fingerprint}`,
    title: "我们一起通过了一次项目验证",
    body: "本次项目验证通过。",
    sourceId: `project.verify.done:${fingerprint}`,
  };
}


