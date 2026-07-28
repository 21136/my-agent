import type { AgentWsClient, ServerEvent, ShellId } from "../../api/ws";
import { setAgentBusy } from "../../agent-busy";
import { wireComposerAttachments } from "../../composer-attachments";
import { mountFileDrop } from "../../file-drop";
import { renderMarkdown } from "../../markdown";
import { formatUserMessageHtml } from "../../user-message";
import { createChatSession, escapeHtml, turnEndStatusText, checkerVerdictStatusText, type ChatBlock } from "../chat-state";
import "./daily.css";

const FOCUS_TURNS = 2;
const RECALL_TURNS = 3;

function isRecallIntent(intent: string, intentLabel: string): boolean {
  return intent === "recall" || intentLabel.includes("回顾");
}

function recentTurnIndices(blocks: ChatBlock[], k: number): number[] {
  const turns = new Set<number>();
  for (const block of blocks) {
    if (block.kind === "user" || block.kind === "assistant" || block.kind === "assistant-streaming") {
      turns.add(block.turnIndex);
    }
  }
  return [...turns].sort((a, b) => b - a).slice(0, k);
}

export function mountDailyShell(root: HTMLElement, client: AgentWsClient, shellId: ShellId = "daily"): () => void {
  let recallHighlightTurns = new Set<number>();
  let cancelledStatusTimer: number | null = null;

  root.innerHTML = `
    <div class="daily-shell">
      <main class="daily-chat-layer" id="daily-chat" aria-live="polite"></main>
      <footer class="daily-composer" id="daily-composer">
        <div class="daily-composer-toolbar">
          <div class="daily-composer-status" id="daily-status" aria-live="polite"></div>
        </div>
        <div class="daily-composer-row">
          <div class="daily-composer-capsule">
            <button type="button" class="daily-btn" id="daily-stop" hidden>停止</button>
            <textarea class="daily-input" id="daily-input" rows="1" placeholder="输入消息，或拖入文件…"></textarea>
            <button type="button" class="daily-btn daily-btn-accent" id="daily-send">发送 ▶</button>
          </div>
        </div>
      </footer>
      <div class="daily-confirm-glass hidden" id="daily-confirm-glass" role="dialog" aria-modal="true"></div>
    </div>
  `;

  const chatEl = root.querySelector<HTMLElement>("#daily-chat")!;
  const statusEl = root.querySelector<HTMLElement>("#daily-status")!;
  const composer = root.querySelector<HTMLElement>("#daily-composer")!;
  const input = root.querySelector<HTMLTextAreaElement>("#daily-input")!;
  const stopBtn = root.querySelector<HTMLButtonElement>("#daily-stop")!;
  const sendBtn = root.querySelector<HTMLButtonElement>("#daily-send")!;
  const confirmGlass = root.querySelector<HTMLElement>("#daily-confirm-glass")!;
  const shellEl = root.querySelector<HTMLElement>(".daily-shell")!;

  let focusObserver: IntersectionObserver | null = null;

  const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  function syncReducedMotionAttr(): void {
    shellEl.toggleAttribute("data-reduced-motion", motionQuery.matches);
  }
  syncReducedMotionAttr();
  motionQuery.addEventListener("change", syncReducedMotionAttr);

  function scrollToRecallTurns(): void {
    const first = chatEl.querySelector<HTMLElement>(".daily-turn-recall");
    first?.scrollIntoView({ behavior: motionQuery.matches ? "auto" : "smooth", block: "center" });
  }

  const chat = createChatSession(
    { showProcess: false, confirmInBlocks: false },
    {
      onChange: () => {
        renderChat();
        renderConfirm();
        syncShellState();
      },
      onSessionBanner: () => {
        setStatus("就绪");
      },
      onTurnStart: (event) => {
        if (isRecallIntent(event.intent, event.intent_label)) {
          recallHighlightTurns = new Set(recentTurnIndices(chat.model.blocks, RECALL_TURNS));
          renderChat();
          requestAnimationFrame(() => scrollToRecallTurns());
        }
        setStatus("处理中…");
      },
      onCheckerVerdict: (event) => {
        setStatus(checkerVerdictStatusText(event.verdict));
      },
      onToolStart: () => {
        setStatus("处理中…");
      },
      onToolEnd: () => {
        if (!chat.model.confirmPending) {
          setStatus(chat.isWorking() ? "处理中…" : "就绪");
        }
      },
      onAssistantDone: () => {
        recallHighlightTurns = new Set();
        if (!chat.model.confirmPending) {
          setStatus("就绪");
        }
      },
      onConfirmRequest: () => {
        setComposerEnabled(false);
        setStatus("等待确认…");
      },
      onConfirmDone: (choice) => {
        setComposerEnabled(choice !== "cancelled");
        if (choice === "cancelled") {
          setStatus("正在停止…");
          return;
        }
        if (choice === "n" || choice === "timeout" || choice === "stale") {
          setStatus(chat.isWorking() ? "处理中…" : "就绪");
        } else {
          setStatus("执行中…");
        }
      },
      onTurnEnd: (_ok, finishReason) => {
        setComposerEnabled(true);
        if (!chat.model.confirmPending) {
          setTurnEndStatus(finishReason);
        }
      },
      onCancelTimeout: () => {
        setComposerEnabled(true);
        setTurnEndStatus("cancelled");
      },
      onError: () => {
        recallHighlightTurns = new Set();
        setStatus("就绪");
      },
    },
  );

  const fileDrop = mountFileDrop({
    composer,
    client,
    shell: shellId,
    canAccept: () => !chat.model.confirmPending,
    onChange: () => composerWire.syncSendEnabled(),
    onNotice: (text) => setStatus(text),
  });

  const composerWire = wireComposerAttachments({
    input,
    sendBtn,
    client,
    chat,
    fileDrop,
    onStatus: (text) => setStatus(text),
    beforeSend: () => {
      setStatus("处理中…");
      syncShellState();
    },
  });

  function syncShellState(): void {
    const working = chat.isWorking();
    shellEl.classList.toggle("is-working", working);
    stopBtn.hidden = !(working || chat.model.confirmPending);
    stopBtn.disabled = chat.model.cancelRequested;
    setAgentBusy(working, "daily");
  }

  function setStatus(text: string): void {
    statusEl.textContent = text;
    statusEl.classList.toggle("is-busy", text === "处理中…" || text === "等待确认…");
  }

  function setTurnEndStatus(finishReason: string): void {
    if (cancelledStatusTimer !== null) {
      window.clearTimeout(cancelledStatusTimer);
      cancelledStatusTimer = null;
    }
    const label = turnEndStatusText(finishReason);
    if (label === null) {
      setStatus("就绪");
      return;
    }
    setStatus(label);
    cancelledStatusTimer = window.setTimeout(() => {
      if (!chat.isWorking() && !chat.model.confirmPending) setStatus("就绪");
      cancelledStatusTimer = null;
    }, 2000);
  }

  function setComposerEnabled(enabled: boolean): void {
    input.disabled = !enabled;
    composer.classList.toggle("disabled", !enabled);
    if (enabled) {
      composerWire.syncSendEnabled();
    } else {
      sendBtn.disabled = true;
    }
  }

  function isRecentTurn(turnIndex: number): boolean {
    return turnIndex >= chat.currentTurnIndex() - (FOCUS_TURNS - 1);
  }

  function setupFocusObserver(): void {
    focusObserver?.disconnect();
    focusObserver = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          const el = entry.target as HTMLElement;
          if (entry.isIntersecting) {
            el.classList.add("in-view");
          }
        }
      },
      { root: chatEl, threshold: 0.15 },
    );
    chatEl.querySelectorAll<HTMLElement>("[data-turn-index]").forEach((el) => {
      focusObserver?.observe(el);
    });
  }

  function renderTurnBlock(block: ChatBlock): string {
    if (block.kind === "user") {
      const dim = isRecentTurn(block.turnIndex) ? "" : " daily-turn-dim";
      const recall = recallHighlightTurns.has(block.turnIndex) ? " daily-turn-recall" : "";
      return `<article class="daily-turn daily-turn-user${dim}${recall}" data-turn-index="${block.turnIndex}">
        <div class="daily-turn-label">你</div>
        <div class="daily-turn-body">${formatUserMessageHtml(block.text)}</div>
      </article>`;
    }
    if (block.kind === "assistant" || block.kind === "assistant-streaming") {
      const dim = isRecentTurn(block.turnIndex) ? "" : " daily-turn-dim";
      const recall = recallHighlightTurns.has(block.turnIndex) ? " daily-turn-recall" : "";
      const streaming = block.kind === "assistant-streaming" ? " daily-turn-streaming" : "";
      return `<article class="daily-turn daily-turn-assistant${dim}${recall}${streaming}" data-turn-index="${block.turnIndex}">
        <div class="daily-turn-label">助手</div>
        <div class="daily-turn-body daily-markdown">${renderMarkdown(block.text)}</div>
      </article>`;
    }
    if (block.kind === "notice") {
      return `<p class="daily-notice">${escapeHtml(block.text)}</p>`;
    }
    return "";
  }

  function bounceLatestUserTurn(): void {
    if (motionQuery.matches) return;
    const turns = chatEl.querySelectorAll<HTMLElement>(".daily-turn-user");
    const last = turns[turns.length - 1];
    if (!last) return;
    last.classList.add("daily-turn-enter");
    last.addEventListener(
      "animationend",
      () => {
        last.classList.remove("daily-turn-enter");
      },
      { once: true },
    );
  }

  function renderChat(): void {
    chatEl.innerHTML = chat.model.blocks.map(renderTurnBlock).join("");
    setupFocusObserver();
    chatEl.scrollTop = chatEl.scrollHeight;
  }

  function renderConfirm(): void {
    const confirm = chat.model.confirmOverlay;
    if (!confirm) {
      confirmGlass.classList.add("hidden");
      confirmGlass.innerHTML = "";
      return;
    }

    confirmGlass.classList.remove("hidden");
    const disabled = confirm.resolved ? "disabled" : "";
    const resolved = confirm.resolved
      ? `<div class="daily-confirm-resolved">${escapeHtml(confirm.resolved)}</div>`
      : `
        <div class="daily-confirm-actions">
          <button type="button" class="daily-btn daily-btn-accent" data-confirm="y" data-id="${confirm.requestId}" ${disabled}>同意</button>
          <button type="button" class="daily-btn daily-btn-danger" data-confirm="n" data-id="${confirm.requestId}" ${disabled}>拒绝</button>
          ${
            confirm.allowApproveAll
              ? `<button type="button" class="daily-btn" data-confirm="a" data-id="${confirm.requestId}" ${disabled}>本会话均允许</button>`
              : ""
          }
        </div>`;

    confirmGlass.innerHTML = `
      <div class="daily-confirm-card ${confirm.resolved ? "resolved" : ""}">
        <div class="daily-confirm-title">工具确认</div>
        <pre class="daily-confirm-preview">${escapeHtml(confirm.preview)}</pre>
        ${resolved}
      </div>
    `;
  }

  sendBtn.addEventListener("click", () => {
    composerWire.sendCurrentMessage();
    bounceLatestUserTurn();
  });
  stopBtn.addEventListener("click", () => {
    if (!chat.requestCancel()) return;
    setComposerEnabled(false);
    setStatus("正在停止…");
    try {
      client.sendTurnCancel();
    } catch (err) {
      chat.model.cancelRequested = false;
      setComposerEnabled(!chat.model.confirmPending);
      setStatus(`停止发送失败：${err instanceof Error ? err.message : String(err)}`);
      syncShellState();
    }
  });
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && !ev.shiftKey) {
      ev.preventDefault();
      composerWire.sendCurrentMessage();
      bounceLatestUserTurn();
    }
  });

  confirmGlass.addEventListener("click", (ev) => {
    const target = ev.target as HTMLElement | null;
    const btn = target?.closest<HTMLButtonElement>("[data-confirm]");
    if (!btn || btn.disabled) return;
    const id = btn.dataset.id;
    const choice = btn.dataset.confirm as "y" | "n" | "a" | undefined;
    if (!id || !choice) return;
    if (!chat.submitConfirm(id, choice)) {
      setStatus("请点最新确认");
      return;
    }
    try {
      client.sendConfirm(id, choice);
      setStatus(choice === "n" ? "已提交拒绝…" : "确认中…");
      syncShellState();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      chat.model.blocks.push({ kind: "notice", text: `确认发送失败：${message}` });
      chat.model.confirmSubmitting = false;
      if (chat.model.confirmOverlay?.requestId === id) {
        chat.model.confirmOverlay = { ...chat.model.confirmOverlay, resolved: undefined };
      }
      renderChat();
      renderConfirm();
      syncShellState();
    }
  });

  const off = client.onEvent((event: ServerEvent) => {
    if (!client.isActiveShell(shellId)) return;
    if (event.type === "evolve.proposals") return;
    if (event.type === "reasoning.delta") {
      if (!chat.model.cancelRequested) {
        setStatus("处理中…");
      }
      syncShellState();
      return;
    }
    chat.handleEvent(event);
  });

  renderChat();
  renderConfirm();
  setComposerEnabled(true);
  setStatus("已连接");
  syncShellState();

  return () => {
    if (cancelledStatusTimer !== null) window.clearTimeout(cancelledStatusTimer);
    fileDrop.destroy();
    off();
    motionQuery.removeEventListener("change", syncReducedMotionAttr);
    focusObserver?.disconnect();
    shellEl.classList.remove("is-working");
    setAgentBusy(false);
    root.innerHTML = "";
  };
}

/** @deprecated Use mountDailyShell */
export const mountDailyPlaceholder = mountDailyShell;
