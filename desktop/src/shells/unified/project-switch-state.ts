/** Pure project-switch isolation helpers. No DOM. */

export interface ProjectSwitchFilterState {
  projectId: string;
  switchInProgress: boolean;
  pendingPickerId: string;
  switchOverlayProjectId: string;
}

export function pendingSwitchProjectId(state: ProjectSwitchFilterState): string {
  return (state.pendingPickerId || state.switchOverlayProjectId || "").trim();
}

/** Event filter target: pending switch wins; otherwise the bound project. */
export function expectedProjectIdForEvents(state: ProjectSwitchFilterState): string {
  const pending = pendingSwitchProjectId(state);
  if (state.switchInProgress || pending) return pending;
  return (state.projectId || "").trim();
}

export function shouldApplyProjectEvent(
  state: ProjectSwitchFilterState,
  eventProjectId: string | null | undefined,
): boolean {
  const incoming = (eventProjectId || "").trim();
  const expected = expectedProjectIdForEvents(state);
  if (expected && incoming && incoming !== expected) return false;
  return true;
}

export function isCrossProjectSession(
  currentProjectId: string,
  sessionProjectId: string | null | undefined,
): boolean {
  const current = currentProjectId.trim();
  const next = (sessionProjectId || "").trim();
  return Boolean(next && current && next !== current);
}
