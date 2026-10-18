export type CandidateReady<TProcess> = {
  proc: TProcess;
  info: { host: string; port: number };
};

export type CandidatePromotionResult =
  | { ok: true; host: string; port: number }
  | { ok: false; message?: string };

export async function promoteCandidateWithRestart<TProcess>({
  candidateRoot,
  candidate,
  stopCandidate,
  startPromoted,
}: {
  candidateRoot: string;
  candidate: CandidateReady<TProcess>;
  stopCandidate: (candidate: CandidateReady<TProcess>, root: string) => void | Promise<void>;
  startPromoted: (root: string) => Promise<CandidatePromotionResult>;
}): Promise<CandidatePromotionResult> {
  await stopCandidate(candidate, candidateRoot);
  return startPromoted(candidateRoot);
}
