/** Global context-switch confirm overlay (Phase 19 · all shells). */

import type { AgentWsClient, ServerEvent } from "./api/ws";
import { SHELL_LABELS, type ShellId } from "./settings";
import "./context-switch-overlay.css";

export type ContextSwitchOverlayHandlers = {
  /** After confirmed shell.switch — sync chrome + visible shell. */
  onShellApplied?: (shell: ShellId) => void;
};

type OverlayState = {
  requestId: string;
  title: string;
  message: string;
  action: string;
  target: string;
  busy: boolean;
};

export function mountContextSwitchOverlay(
  parent: HTMLElement,
  client: AgentWsClient,
  handlers: ContextSwitchOverlayHandlers = {},
): () => void {
  const root = document.createElement("div");
  root.id = "context-switch-overlay-host";
  parent.appendChild(root);

  let state: OverlayState | null = null;

  function render(): void {
    if (!state) {
      root.innerHTML = "";
      root.classList.add("hidden");
      return;
    }
    root.classList.remove("hidden");
    root.innerHTML = `
      <div class="ctx-switch-card" role="dialog" aria-modal="true" aria-labelledby="ctx-switch-title">
        <h3 id="ctx-switch-title" class="ctx-switch-title">${escapeHtml(state.title)}</h3>
        <pre class="ctx-switch-body">${escapeHtml(state.message)}</pre>
        <div class="ctx-switch-actions">
          <button type="button" class="ctx-switch-btn ctx-switch-btn-primary" data-choice="y"${state.busy ? " disabled" : ""}>确认换线</button>
          <button type="button" class="ctx-switch-btn" data-choice="n"${state.busy ? " disabled" : ""}>拒绝</button>
        </div>
      </div>
    `;
  }

  function showRequest(event: Extract<ServerEvent, { type: "context.switch.request" }>): void {
    const effects = (event.side_effects ?? []).map((line) => `· ${line}`).join("\n");
    const current = event.current?.project_id
      ? `当前：${event.current.shell ?? "?"} · ${event.current.project_id}`
      : `当前外壳：${event.current?.shell ?? "?"}`;
    const reason = event.reason ? `原因：${event.reason}` : "";
    state = {
      requestId: event.request_id,
      title: event.title || "换线确认",
      message: [current, reason, effects].filter(Boolean).join("\n"),
      action: event.action,
      target: event.target,
      busy: false,
    };
    render();
  }

  function clear(): void {
    state = null;
    render();
  }

  root.addEventListener("click", (ev) => {
    const btn = (ev.target as HTMLElement | null)?.closest?.("[data-choice]") as
      | HTMLButtonElement
      | null;
    if (!btn || !state || state.busy) return;
    const choice = btn.dataset.choice === "y" ? "y" : "n";
    state.busy = true;
    render();
    try {
      client.sendContextSwitchResponse(state.requestId, choice);
    } catch {
      state.busy = false;
      render();
    }
    if (choice === "n") {
      // done event will clear; optimistic hide is fine
    }
  });

  const off = client.onEvent((event: ServerEvent) => {
    if (event.type === "context.switch.request") {
      showRequest(event);
      return;
    }
    if (event.type === "context.switch.done") {
      const appliedShell =
        event.applied && event.action === "shell.switch"
          ? (event.shell || event.target)
          : null;
      clear();
      if (
        appliedShell &&
        (appliedShell === "grow" ||
          appliedShell === "daily" ||
          appliedShell === "project" ||
          appliedShell === "govern")
      ) {
        handlers.onShellApplied?.(appliedShell);
      }
      return;
    }
  });

  render();
  return () => {
    off();
    root.remove();
  };
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function shellLabel(shell: string): string {
  return SHELL_LABELS[shell as ShellId] ?? shell;
}
