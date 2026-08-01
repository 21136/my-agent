import type { AgentWsClient, ServerEvent, ShellId } from "../../api/ws";
import { wireComposerAttachments } from "../../composer-attachments";
import { mountFileDrop } from "../../file-drop";
import "../../file-drop.css";
import { renderMarkdown } from "../../markdown";
import { formatUserMessageHtml } from "../../user-message";
import { createChatSession, escapeHtml, turnEndStatusText, checkerVerdictStatusText, type ChatBlock, type ChatSession } from "../chat-state";
import {
  classifyPetRoute,
  formatRouteNotice,
  resolveWorkbenchShell,

} from "./pet-route";
import "./pet.css";

const MAX_VISIBLE_TURNS = 6;
const RECALL_TURNS = 4;
const BACKEND_SHELL = "daily" as const;

type PetMood = "idle" | "listening" | "busy" | "nudge";

/** CC0 · OpenGameArt «Xenia the Linux Fox sprites» (Cawfeecrow / Alan Mackey) */
const PET_ASSET = "/pet-assets/xenia";
const PET_SPRITES: Record<PetMood, string> = {
  idle: `${PET_ASSET}/xenia-neutral-lookingforward.png`,
  listening: `${PET_ASSET}/xenia-wink-left.png`,
  busy: `${PET_ASSET}/xenia-amazed.png`,
  nudge: `${PET_ASSET}/xenia-surprised.png`,
};

function isRecallIntent(intent: string, intentLabel: string): boolean {
  return intent === "recall" || intentLabel.includes("回顾");
}

function statusForIntent(intent: string, intentLabel: string): string {
  if (isRecallIntent(intent, intentLabel)) return "正在回顾…";
  if (intent === "qa") return "想一下…";
  if (intent === "execute") return "正在查…";
  return "处理中…";
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

export function mountPetShell(root: HTMLElement, client: AgentWsClient): () => void {
  document.documentElement.classList.add("pet-root");

  let bubbleOpen = false;
  let recallActive = false;
  let recallHighlightTurns = new Set<number>();
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let pendingRoute: any = null;
  let cancelledStatusTimer: number | null = null;

  root.innerHTML = `
    <div class="pet-shell" data-mood="idle">
      <section class="pet-bubble hidden" id="pet-bubble" aria-label="聊天">
        <div class="pet-bubble-head">
          <span class="pet-status" id="pet-status">就绪</span>
          <button type="button" id="pet-workbench">工作台</button>
        </div>
        <div class="pet-route-notice hidden" id="pet-route-notice" role="status"></div>
        <div class="pet-chat" id="pet-chat" aria-live="polite"></div>
        <div class="pet-confirm hidden" id="pet-confirm"></div>
        <footer class="pet-composer" id="pet-composer">
          <div class="pet-composer-row">
            <button type="button" class="pet-send pet-stop" id="pet-stop" hidden>停止</button>
            <textarea class="pet-input" id="pet-input" rows="1" placeholder="聊点什么，或拖入文件…"></textarea>
            <button type="button" class="pet-send" id="pet-send">发送</button>
          </div>
        </footer>
      </section>
      <div class="pet-orb-wrap">
        <button type="button" class="pet-orb" id="pet-orb" aria-label="伴侶，点击展开聊天">
          <img class="pet-sprite" id="pet-sprite" src="${PET_SPRITES.idle}" alt="" draggable="false" width="96" height="96" />
        </button>
      </div>
    </div>
  `;

  const shellEl = root.querySelector<HTMLElement>(".pet-shell")!;
  const bubbleEl = root.querySelector<HTMLElement>("#pet-bubble")!;
  const chatEl = root.querySelector<HTMLElement>("#pet-chat")!;
  const confirmEl = root.querySelector<HTMLElement>("#pet-confirm")!;
  const routeNoticeEl = root.querySelector<HTMLElement>("#pet-route-notice")!;
  const statusEl = root.querySelector<HTMLElement>("#pet-status")!;
  const composer = root.querySelector<HTMLElement>("#pet-composer")!;
  const input = root.querySelector<HTMLTextAreaElement>("#pet-input")!;
  const stopBtn = root.querySelector<HTMLButtonElement>("#pet-stop")!;
  const sendBtn = root.querySelector<HTMLButtonElement>("#pet-send")!;
  const orbBtn = root.querySelector<HTMLButtonElement>("#pet-orb")!;
  const spriteImg = root.querySelector<HTMLImageElement>("#pet-sprite")!;
  const workbenchBtn = root.querySelector<HTMLButtonElement>("#pet-workbench")!;

  const api = window.myAgentDesktop;

  function setMood(next: PetMood): void {
    shellEl.dataset.mood = next;
    spriteImg.src = PET_SPRITES[next];
  }

  function syncMood(): void {
    stopBtn.hidden = !(chat.isWorking() || chat.model.confirmPending);
    stopBtn.disabled = chat.model.cancelRequested;
    if (pendingRoute) {
      setMood("nudge");
      return;
    }
    if (chat.model.confirmPending) {
      setMood("nudge");
      return;
    }
    if (chat.isWorking()) {
      setMood("busy");
    } else {
      setMood("idle");
    }
  }

  function setBubbleOpen(open: boolean): void {
    bubbleOpen = open;
    bubbleEl.classList.toggle("hidden", !open);
    api?.petSetBounds?.(open ? "expanded" : "collapsed");
    if (open) {
      api?.petSetIgnoreMouseEvents?.(false);
      input.focus();
    } else {
      api?.petSetIgnoreMouseEvents?.(true);
    }
    renderChat();
  }

  function bindPointerPassthrough(): void {
    const enter = () => api?.petSetIgnoreMouseEvents?.(false);
    const leave = () => {
      if (!bubbleOpen) {
        api?.petSetIgnoreMouseEvents?.(true);
      }
    };
    root.addEventListener("mouseenter", enter);
    root.addEventListener("mouseleave", leave);
    orbBtn.addEventListener("mouseenter", enter);
  }

  function setStatus(text: string): void {
    statusEl.textContent = text;
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
    if (enabled) {
      composerWire.syncSendEnabled();
    } else {
      sendBtn.disabled = true;
    }
    composer.classList.toggle("disabled", !enabled);
  }

  function clearPendingRoute(): void {
    pendingRoute = null;
    renderRouteNotice();
    syncMood();
  }

  function renderRouteNotice(): void {
    if (!pendingRoute) {
      routeNoticeEl.classList.add("hidden");
      routeNoticeEl.innerHTML = "";
      return;
    }

    const tier = classifyPetRoute(pendingRoute);
    const text = formatRouteNotice(pendingRoute, tier);
    routeNoticeEl.classList.remove("hidden");
    routeNoticeEl.innerHTML = `
      <p class="pet-route-notice-text">${escapeHtml(text)}</p>
      <div class="pet-route-notice-actions">
        <button type="button" id="pet-route-go">去工作台</button>
        <button type="button" id="pet-route-dismiss">知道了</button>
      </div>
    `;

    routeNoticeEl.querySelector<HTMLButtonElement>("#pet-route-go")?.addEventListener("click", () => {
      void goToWorkbench(pendingRoute!.shell);
    });
    routeNoticeEl.querySelector<HTMLButtonElement>("#pet-route-dismiss")?.addEventListener("click", () => {
      clearPendingRoute();
    });
  }

  async function goToWorkbench(target: string): Promise<void> {
    const { shell, mappedNotice } = resolveWorkbenchShell(target);
    try {
      localStorage.setItem("active_shell", shell);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      chat.model.blocks.push({ kind: "notice", text: `切换外壳失败：${message}` });
      renderChat();
      return;
    }

    clearPendingRoute();

    if (mappedNotice) {
      chat.model.blocks.push({ kind: "notice", text: mappedNotice });
      renderChat();
    }

    try {
      await api?.openWorkbench?.();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      chat.model.blocks.push({ kind: "notice", text: `打开工作台失败：${message}` });
      renderChat();
    }
  }

  function handleUiRoute(evt: any): void {
    const tier = classifyPetRoute(evt);
    if (tier === "ignore") return;

    if (tier === "auto") {
      if (!bubbleOpen) {
        setBubbleOpen(true);
      }
      void goToWorkbench(evt.shell);
      return;
    }

    pendingRoute = evt;
    if (!bubbleOpen) {
      setBubbleOpen(true);
    }
    renderRouteNotice();
    syncMood();
  }

  function renderTurnBlock(block: ChatBlock): string {
    if (block.kind === "user") {
      const recall = recallHighlightTurns.has(block.turnIndex) ? " pet-turn-recall" : "";
      return `<article class="pet-turn pet-turn-user${recall}" data-turn-index="${block.turnIndex}">
      <div class="pet-turn-label">你</div>
      <div class="pet-turn-body">${formatUserMessageHtml(block.text)}</div>
    </article>`;
    }
    if (block.kind === "assistant" || block.kind === "assistant-streaming") {
      const recall = recallHighlightTurns.has(block.turnIndex) ? " pet-turn-recall" : "";
      const streaming = block.kind === "assistant-streaming" ? " pet-turn-streaming" : "";
      return `<article class="pet-turn pet-turn-assistant${recall}${streaming}" data-turn-index="${block.turnIndex}">
      <div class="pet-turn-label">助手</div>
      <div class="pet-turn-body pet-markdown">${renderMarkdown(block.text)}</div>
    </article>`;
    }
    if (block.kind === "notice") {
      return `<p class="pet-notice">${escapeHtml(block.text)}</p>`;
    }
    return "";
  }

  function visibleTurnLimit(): number {
    if (!recallActive && recallHighlightTurns.size === 0) {
      return MAX_VISIBLE_TURNS;
    }
    const span =
      recallHighlightTurns.size > 0
        ? Math.max(...recallHighlightTurns) - Math.min(...recallHighlightTurns) + 1
        : RECALL_TURNS;
    return Math.max(MAX_VISIBLE_TURNS, span + 2, RECALL_TURNS + 2);
  }

  function visibleBlocks(): ChatBlock[] {
    const keep = new Set(recentTurnIndices(chat.model.blocks, visibleTurnLimit()));
    for (const turnIndex of recallHighlightTurns) {
      keep.add(turnIndex);
    }
    return chat.model.blocks.filter((block) => {
      if (block.kind === "notice" || block.kind === "confirm" || block.kind === "process") {
        return true;
      }
      if (block.kind === "user" || block.kind === "assistant" || block.kind === "assistant-streaming") {
        return keep.has(block.turnIndex);
      }
      return false;
    });
  }

  function renderChat(): void {
    chatEl.innerHTML = visibleBlocks().map(renderTurnBlock).join("");
    chatEl.scrollTop = chatEl.scrollHeight;
  }

  function renderConfirm(): void {
    const confirm = chat.model.confirmOverlay;
    if (!confirm) {
      confirmEl.classList.add("hidden");
      confirmEl.innerHTML = "";
      return;
    }

    confirmEl.classList.remove("hidden");
    const disabled = confirm.resolved ? "disabled" : "";
    const resolved = confirm.resolved
      ? `<div>${escapeHtml(confirm.resolved)}</div>`
      : `<div class="pet-confirm-actions">
          <button type="button" data-confirm="y" data-id="${confirm.requestId}" ${disabled}>同意</button>
          <button type="button" data-confirm="n" data-id="${confirm.requestId}" ${disabled}>拒绝</button>
          ${
            confirm.allowApproveAll
              ? `<button type="button" data-confirm="a" data-id="${confirm.requestId}" ${disabled}>本会话均允许</button>`
              : ""
          }
        </div>`;

    confirmEl.innerHTML = `
      <div class="pet-confirm-title">工具确认</div>
      <pre class="pet-confirm-preview">${escapeHtml(confirm.preview)}</pre>
      ${resolved}
    `;
  }

  let chat: ChatSession;
  let composerWire: ReturnType<typeof wireComposerAttachments>;

  chat = createChatSession(
    { showProcess: false, confirmInBlocks: false },
    {
      onChange: () => {
        renderChat();
        renderConfirm();
        syncMood();
        (window as Window & { __myAgentIsBusy?: () => boolean }).__myAgentIsBusy = () =>
          chat.isWorking() || chat.model.confirmPending;
      },
      onSessionBanner: () => setStatus("就绪"),
      onTurnStart: (event) => {
        if (isRecallIntent(event.intent, event.intent_label)) {
          recallActive = true;
          recallHighlightTurns = new Set(recentTurnIndices(chat.model.blocks, RECALL_TURNS));
          renderChat();
        }
        setStatus(statusForIntent(event.intent, event.intent_label));
      },
      onCheckerVerdict: (event) => {
        setStatus(checkerVerdictStatusText(event.verdict));
      },
      onToolStart: () => {
        if (statusEl.textContent === "就绪") {
          setStatus("正在查…");
        }
      },
      onToolEnd: () => {
        if (!chat.model.confirmPending) {
          setStatus(chat.isWorking() ? "正在查…" : "就绪");
        }
      },
      onAssistantDone: () => {
        recallActive = false;
        recallHighlightTurns = new Set();
        renderChat();
        if (!chat.model.confirmPending) {
          setStatus("就绪");
        }
      },
      onConfirmRequest: () => {
        setComposerEnabled(false);
        setStatus("等你确认…");
        if (!bubbleOpen) {
          setBubbleOpen(true);
        }
      },
      onConfirmDone: (choice) => {
        setComposerEnabled(choice !== "cancelled");
        if (choice === "cancelled") {
          setStatus("正在停止…");
        } else if (choice === "n" || choice === "timeout" || choice === "stale") {
          setStatus(chat.isWorking() ? "正在查…" : "就绪");
        } else {
          setStatus("处理中…");
        }
        if (confirmDismissTimer !== null) {
          window.clearTimeout(confirmDismissTimer);
        }
        confirmDismissTimer = window.setTimeout(() => {
          confirmDismissTimer = null;
          if (chat.model.confirmOverlay?.resolved) {
            chat.model.confirmOverlay = null;
            renderConfirm();
          }
        }, 1200);
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
        recallActive = false;
        recallHighlightTurns = new Set();
        setStatus("就绪");
      },
    },
  );

  const fileDrop = mountFileDrop({
    composer,
    client,
    shell: BACKEND_SHELL,
    canAccept: () => bubbleOpen && !chat.model.confirmPending,
    onChange: () => composerWire.syncSendEnabled(),
    onNotice: (text) => {
      if (!bubbleOpen) {
        setBubbleOpen(true);
      }
      chat.model.blocks.push({ kind: "notice", text });
      renderChat();
    },
  });

  composerWire = wireComposerAttachments({
    input,
    sendBtn,
    client,
    chat,
    fileDrop,
    onStatus: (text) => setStatus(text),
    beforeSend: () => {
      clearPendingRoute();
      setMood("listening");
      setStatus("处理中…");
      syncMood();
    },
  });

  function sendCurrentMessage(): void {
    composerWire.sendCurrentMessage();
  }

  orbBtn.addEventListener("click", () => {
    setBubbleOpen(!bubbleOpen);
  });

  workbenchBtn.addEventListener("click", () => {
    void api?.openWorkbench?.();
  });

  sendBtn.addEventListener("click", sendCurrentMessage);
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
      syncMood();
    }
  });
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && !ev.shiftKey) {
      ev.preventDefault();
      sendCurrentMessage();
    }
  });

  confirmEl.addEventListener("click", (ev) => {
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
      syncMood();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      chat.model.blocks.push({ kind: "notice", text: `确认发送失败：${message}` });
      chat.model.confirmSubmitting = false;
      if (chat.model.confirmOverlay?.requestId === id) {
        chat.model.confirmOverlay = { ...chat.model.confirmOverlay, resolved: undefined };
      }
      renderChat();
      renderConfirm();
      syncMood();
    }
  });

  const off = client.onEvent((event: ServerEvent) => {
    if (event.type === "evolve.proposals") return;
    chat.handleEvent(event);
  });

  localStorage.setItem("active_shell", BACKEND_SHELL);

  bindPointerPassthrough();
  api?.petSetIgnoreMouseEvents?.(true);
  renderChat();
  renderConfirm();
  setComposerEnabled(true);
  setStatus("已连接");
  syncMood();

  let idleBlinkTimer: number | null = null;
  let confirmDismissTimer: number | null = null;
  function scheduleIdleBlink(): void {
    if (idleBlinkTimer !== null) {
      window.clearTimeout(idleBlinkTimer);
      idleBlinkTimer = null;
    }
    if (shellEl.dataset.mood !== "idle") return;
    idleBlinkTimer = window.setTimeout(() => {
      idleBlinkTimer = null;
      if (shellEl.dataset.mood !== "idle") return;
      spriteImg.src = `${PET_ASSET}/xenia-wink-right.png`;
      window.setTimeout(() => {
        if (shellEl.dataset.mood === "idle") {
          spriteImg.src = PET_SPRITES.idle;
        }
        scheduleIdleBlink();
      }, 280);
    }, 4500 + Math.random() * 2500);
  }
  scheduleIdleBlink();

  const moodObserver = new MutationObserver(() => {
    if (shellEl.dataset.mood === "idle") {
      scheduleIdleBlink();
    } else if (idleBlinkTimer !== null) {
      window.clearTimeout(idleBlinkTimer);
      idleBlinkTimer = null;
    }
  });
  moodObserver.observe(shellEl, { attributes: true, attributeFilter: ["data-mood"] });

  return () => {
    off();
    fileDrop.destroy();
    moodObserver.disconnect();
    if (idleBlinkTimer !== null) {
      window.clearTimeout(idleBlinkTimer);
    }
    if (confirmDismissTimer !== null) {
      window.clearTimeout(confirmDismissTimer);
    }
    if (cancelledStatusTimer !== null) {
      window.clearTimeout(cancelledStatusTimer);
    }
    document.documentElement.classList.remove("pet-root");
    root.innerHTML = "";
    delete (window as Window & { __myAgentIsBusy?: () => boolean }).__myAgentIsBusy;
  };
}
