import type { AgentWsClient, ProposalItem } from "../../api/ws";
import { escapeHtml } from "../chat-state";

export interface ProposalsState {
  proposals: ProposalItem[];
  proposalIndex: number;
  expandOpen: boolean;
}

export function currentProposal(state: ProposalsState): ProposalItem | null {
  if (!state.proposals.length) return null;
  const idx = Math.min(state.proposalIndex, state.proposals.length - 1);
  return state.proposals[idx] ?? null;
}

export function nextProposalIndex(state: ProposalsState): number {
  if (state.proposalIndex < state.proposals.length - 1) {
    return state.proposalIndex + 1;
  }
  return 0;
}

export function renderProposals(
  container: HTMLElement,
  state: ProposalsState,
  client: AgentWsClient,
): void {
  const proposal = currentProposal(state);
  if (!state.expandOpen || !proposal) {
    container.classList.add("hidden");
    container.innerHTML = "";
    return;
  }

  container.classList.remove("hidden");
  const target = proposal.target_path
    ? `<div class="text-muted">${escapeHtml(proposal.target_path)}</div>`
    : "";

  container.innerHTML = `
    <div class="unified-surface">
      <div class="unified-expand-title">${escapeHtml(proposal.proposal_id)}</div>
      <div class="text-muted">${escapeHtml(proposal.summary)}</div>
      ${target}
      <div class="unified-expand-actions">
        <button type="button" class="unified-btn unified-btn-accent" data-action="accept">接受</button>
        <button type="button" class="unified-btn unified-btn-danger" data-action="reject">拒绝</button>
        <button type="button" class="unified-btn" data-action="next">下一条</button>
        <button type="button" class="unified-btn" data-action="close">收起</button>
      </div>
    </div>
  `;

  container.querySelector<HTMLButtonElement>('[data-action="accept"]')?.addEventListener("click", () => {
    client.acceptProposal(proposal.proposal_id);
  });
  container.querySelector<HTMLButtonElement>('[data-action="reject"]')?.addEventListener("click", () => {
    client.rejectProposal(proposal.proposal_id);
  });
  container.querySelector<HTMLButtonElement>('[data-action="next"]')?.addEventListener("click", () => {
    // caller should update proposalIndex and re-render
    const nextBtn = container.querySelector<HTMLButtonElement>('[data-action="next"]');
    nextBtn?.dispatchEvent(new CustomEvent("proposals:next"));
  });
  container.querySelector<HTMLButtonElement>('[data-action="close"]')?.addEventListener("click", () => {
    const closeBtn = container.querySelector<HTMLButtonElement>('[data-action="close"]');
    closeBtn?.dispatchEvent(new CustomEvent("proposals:close"));
  });
}
