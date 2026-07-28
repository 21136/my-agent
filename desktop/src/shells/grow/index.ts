import type { AgentWsClient, ProposalItem, ServerEvent, ShellId } from "../../api/ws";
import { setAgentBusy } from "../../agent-busy";
import { wireComposerAttachments } from "../../composer-attachments";
import { mountFileDrop } from "../../file-drop";
import { renderMarkdown } from "../../markdown";
import { formatUserMessageHtml } from "../../user-message";
import { createChatSession, escapeHtml, turnEndStatusText, checkerVerdictStatusText, type ChatBlock } from "../chat-state";
import "./grow.css";

export function mountGrowShell(root: HTMLElement, client: AgentWsClient, shellId: ShellId = "grow"): () => void {
  const ui = {
    proposals: [] as ProposalItem[],
    proposalIndex: 0,
    expandOpen: false,
    intentLabel: "",
    checkerLabel: "",
    memoryLabel: "",
    projectLabel: "",
    status: "连接中…",
  };
  let cancelledStatusTimer: number | null = null;

  const chat = createChatSession(
    { showProcess: true, confirmInBlocks: true },
    {
      onChange: () => {
        renderChat();
        syncWorkingVisual();
      },
      onTurnStart: (event) => {
        ui.intentLabel = event.intent_label;
        ui.checkerLabel = "";
        renderTopbar();
        setStatus(event.intent_label);
      },
      onCheckerVerdict: (event) => {
        ui.checkerLabel = checkerVerdictStatusText(event.verdict);
        renderTopbar();
        setStatus(ui.checkerLabel);
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
      onToolStart: () => {
        setStatus("处理中…");
      },
      onToolEnd: () => {
        if (!chat.model.confirmPending) {
          setStatus(chat.isWorking() ? "处理中…" : "就绪");
        }
      },
      onAssistantDone: () => {
        if (chat.model.cancelRequested) return;
        if (!chat.model.confirmPending) {
          setStatus("就绪");
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
        setComposerEnabled(true);
        setStatus("错误");
      },
    },
  );

  root.innerHTML = `
    <div class="grow-shell">
      <header class="grow-topbar" id="grow-topbar"></header>
      <section class="grow-expand hidden" id="grow-expand"></section>
      <main class="grow-chat" id="grow-chat"></main>
      <div class="grow-status" id="grow-status"></div>
      <footer class="grow-composer" id="grow-composer">
        <button type="button" class="grow-btn" id="grow-stop" hidden>停止</button>
        <textarea class="grow-input" id="grow-input" rows="1" placeholder="输入消息，或拖入文件…"></textarea>
        <button type="button" class="grow-btn grow-btn-accent" id="grow-send">发送</button>
      </footer>
    </div>
  `;

  const topbar = root.querySelector<HTMLElement>("#grow-topbar")!;
  const expand = root.querySelector<HTMLElement>("#grow-expand")!;
  const chatEl = root.querySelector<HTMLElement>("#grow-chat")!;
  const statusEl = root.querySelector<HTMLElement>("#grow-status")!;
  const composer = root.querySelector<HTMLElement>("#grow-composer")!;
  const input = root.querySelector<HTMLTextAreaElement>("#grow-input")!;
  const stopBtn = root.querySelector<HTMLButtonElement>("#grow-stop")!;
  const sendBtn = root.querySelector<HTMLButtonElement>("#grow-send")!;
  const shell = root.querySelector<HTMLElement>(".grow-shell")!;

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
      ui.intentLabel = "";
      setStatus("处理中…");
    },
  });

  function syncWorkingVisual(): void {
    const working = chat.isWorking();
    shell.classList.toggle("is-working", working);
    stopBtn.hidden = !(working || chat.model.confirmPending);
    stopBtn.disabled = chat.model.cancelRequested;
    const busyShell =
      root.dataset.shellVariant === "project" ? "project" : "grow";
    setAgentBusy(working, busyShell);
  }

  function setStatus(text: string): void {
    ui.status = text;
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
    composer.classList.toggle("disabled", !enabled);
    if (enabled) {
      composerWire.syncSendEnabled();
    } else {
      sendBtn.disabled = true;
    }
  }

  function currentProposal(): ProposalItem | null {
    if (!ui.proposals.length) return null;
    const idx = Math.min(ui.proposalIndex, ui.proposals.length - 1);
    return ui.proposals[idx] ?? null;
  }

  function formatMemoryLabel(event: {
    message_count: number;
    memory_mode_label: string;
    digest_sections?: number;
    keep_turns?: number;
  }): string {
    let label = `${event.message_count} 条 · ${event.memory_mode_label}`;
    if (event.digest_sections && event.keep_turns) {
      label += ` · 保留 ${event.keep_turns} 轮`;
    }
    return label;
  }

  function renderTopbar(): void {
    const proposal = ui.proposals[0];
    const project = ui.projectLabel
      ? `<span class="grow-topbar-mark" aria-hidden="true">◆</span><span class="grow-topbar-text">${escapeHtml(ui.projectLabel)}</span>`
      : "";
    const memory = ui.memoryLabel
      ? `<span class="grow-topbar-muted">${escapeHtml(ui.memoryLabel)}</span>`
      : "";
    const intent = ui.intentLabel
      ? `<span class="grow-topbar-muted">${escapeHtml(ui.intentLabel)}</span>`
      : "";
    const checker = ui.checkerLabel
      ? `<span class="grow-topbar-muted">${escapeHtml(ui.checkerLabel)}</span>`
      : "";
    const meta = [project, intent, checker, memory].filter(Boolean).join(" · ");
    if (!proposal) {
      topbar.innerHTML = meta
        ? `<span class="grow-topbar-text">${meta}</span>`
        : `<span class="grow-topbar-text grow-topbar-muted">当前无待处理</span>`;
      return;
    }
    const count = ui.proposals.length;
    const label = proposal.summary || proposal.proposal_id;
    topbar.innerHTML = `
      <span class="grow-topbar-mark" aria-hidden="true">■</span>
      <span class="grow-topbar-text">当前：${count} 条 proposal 待接受 · ${escapeHtml(label)}</span>
      <button type="button" class="grow-btn grow-btn-accent" id="grow-open-proposal">去处理</button>
    `;
    topbar.querySelector<HTMLButtonElement>("#grow-open-proposal")?.addEventListener("click", () => {
      ui.expandOpen = true;
      renderExpand();
    });
  }

  function renderExpand(): void {
    const proposal = currentProposal();
    if (!ui.expandOpen || !proposal) {
      expand.classList.add("hidden");
      expand.innerHTML = "";
      return;
    }
    expand.classList.remove("hidden");
    const target = proposal.target_path
      ? `<div class="text-muted">${escapeHtml(proposal.target_path)}</div>`
      : "";
    expand.innerHTML = `
      <div class="grow-surface">
        <div class="grow-expand-title">${escapeHtml(proposal.proposal_id)}</div>
        <div class="text-muted">${escapeHtml(proposal.summary)}</div>
        ${target}
        <div class="grow-expand-actions">
          <button type="button" class="grow-btn grow-btn-accent" data-action="accept">接受</button>
          <button type="button" class="grow-btn grow-btn-danger" data-action="reject">拒绝</button>
          <button type="button" class="grow-btn" data-action="next">下一条</button>
          <button type="button" class="grow-btn" data-action="close">收起</button>
        </div>
      </div>
    `;
    expand.querySelector<HTMLButtonElement>('[data-action="accept"]')?.addEventListener("click", () => {
      client.acceptProposal(proposal.proposal_id);
    });
    expand.querySelector<HTMLButtonElement>('[data-action="reject"]')?.addEventListener("click", () => {
      client.rejectProposal(proposal.proposal_id);
    });
    expand.querySelector<HTMLButtonElement>('[data-action="next"]')?.addEventListener("click", () => {
      if (ui.proposalIndex < ui.proposals.length - 1) {
        ui.proposalIndex += 1;
      } else {
        ui.proposalIndex = 0;
      }
      renderExpand();
    });
    expand.querySelector<HTMLButtonElement>('[data-action="close"]')?.addEventListener("click", () => {
      ui.expandOpen = false;
      renderExpand();
    });
  }

  function renderBlock(block: ChatBlock): string {
    if (block.kind === "user") {
      return `<article class="grow-turn"><div class="grow-turn-label">你</div><div class="grow-turn-body">${formatUserMessageHtml(block.text)}</div></article>`;
    }
    if (block.kind === "assistant" || block.kind === "assistant-streaming") {
      return `<article class="grow-turn"><div class="grow-turn-label">助手</div><div class="grow-turn-body grow-markdown">${renderMarkdown(block.text)}</div></article>`;
    }
    if (block.kind === "notice") {
      return `<p class="text-muted">${escapeHtml(block.text)}</p>`;
    }
    if (block.kind === "process") {
      const lines = block.lines.map((l) => `<div class="grow-process-line">${escapeHtml(l)}</div>`).join("");
      const reasoning = block.reasoning
        ? `<div class="grow-process-reasoning">${escapeHtml(block.reasoning)}</div>`
        : "";
      const title = block.reasoning ? "思考中…" : "过程";
      const toggle = block.collapsed ? "展开" : "收起";
      return `
        <div class="grow-process ${block.collapsed ? "collapsed" : ""}" data-turn="${block.turnKey}">
          <div class="grow-process-header">
            <span>${title}</span>
            <button type="button" class="grow-btn" data-process-toggle="${block.turnKey}">${toggle}</button>
          </div>
          <div class="grow-process-lines">${reasoning}${lines}</div>
        </div>`;
    }
    if (block.kind === "confirm") {
      const disabled = block.resolved ? "disabled" : "";
      const resolved = block.resolved
        ? `<div class="text-muted">${escapeHtml(block.resolved)}</div>`
        : `
        <div class="grow-expand-actions">
          <button type="button" class="grow-btn grow-btn-accent" data-confirm="y" data-id="${block.requestId}" ${disabled}>同意</button>
          <button type="button" class="grow-btn grow-btn-danger" data-confirm="n" data-id="${block.requestId}" ${disabled}>拒绝</button>
          ${
            block.allowApproveAll
              ? `<button type="button" class="grow-btn" data-confirm="a" data-id="${block.requestId}" ${disabled}>本会话 workspace 均允许</button>`
              : ""
          }
        </div>`;
      return `
        <div class="grow-surface grow-confirm ${block.resolved ? "resolved" : ""}">
          <div class="grow-expand-title">工具确认</div>
          <pre class="grow-confirm-preview">${escapeHtml(block.preview)}</pre>
          ${resolved}
        </div>`;
    }
    return "";
  }

  function renderChat(): void {
    chatEl.innerHTML = chat.model.blocks.map(renderBlock).join("");

    chatEl.querySelectorAll<HTMLButtonElement>("[data-process-toggle]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const turnKey = btn.dataset.processToggle;
        if (turnKey) chat.toggleProcessCollapsed(turnKey);
      });
    });

    chatEl.scrollTop = chatEl.scrollHeight;
  }

  sendBtn.addEventListener("click", () => composerWire.sendCurrentMessage());
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
      syncWorkingVisual();
    }
  });
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && !ev.shiftKey) {
      ev.preventDefault();
      composerWire.sendCurrentMessage();
    }
  });

  chatEl.addEventListener("click", (ev) => {
    const target = ev.target as HTMLElement | null;
    const btn = target?.closest<HTMLButtonElement>("[data-confirm]");
    if (!btn || btn.disabled) return;
    const id = btn.dataset.id;
    const choice = btn.dataset.confirm as "y" | "n" | "a" | undefined;
    if (!id || !choice) return;
    if (!chat.submitConfirm(id, choice)) {
      setStatus("请点最新一张工具确认卡");
      return;
    }
    try {
      client.sendConfirm(id, choice);
      setStatus(choice === "n" ? "已提交拒绝…" : "确认中…");
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setStatus(`确认发送失败：${message}`);
      chat.model.confirmSubmitting = false;
      const block = chat.model.blocks.find((b) => b.kind === "confirm" && b.requestId === id);
      if (block?.kind === "confirm") {
        block.resolved = undefined;
      }
      if (chat.model.confirmOverlay?.requestId === id) {
        chat.model.confirmOverlay = { ...chat.model.confirmOverlay, resolved: undefined };
      }
      renderChat();
      syncWorkingVisual();
    }
  });

  const off = client.onEvent((event: ServerEvent) => {
    if (!client.isActiveShell(shellId)) return;
    switch (event.type) {
      case "session.banner":
        chat.handleEvent(event);
        if (event.project_id) {
          const plan = event.project_plan_label ?? "计划待确认";
          ui.projectLabel = `项目 · ${event.project_id} · ${plan}`;
        } else {
          ui.projectLabel = "";
        }
        setStatus(`会话 ${event.session_id} · ${event.turn_mode_label}`);
        renderTopbar();
        break;
      case "session.memory":
        ui.memoryLabel = formatMemoryLabel(event);
        renderTopbar();
        break;
      case "evolve.proposals":
        ui.proposals = event.items;
        ui.proposalIndex = 0;
        if (!ui.proposals.length) ui.expandOpen = false;
        renderTopbar();
        renderExpand();
        break;
      case "tool.start":
        chat.handleEvent(event);
        setStatus(`· ${event.tool}`);
        break;
      case "tool.end":
        chat.handleEvent(event);
        if (!chat.model.confirmPending) {
          setStatus(event.ok ? (chat.isWorking() ? "处理中…" : "就绪") : "工具失败");
        }
        break;
      case "reasoning.delta":
        chat.handleEvent(event);
        if (!chat.model.cancelRequested) {
          setStatus("思考中…");
        }
        break;
      case "assistant.done":
        chat.handleEvent(event);
        if (!chat.model.cancelRequested && !chat.model.confirmPending) {
          setStatus("就绪");
        }
        break;
      case "turn.end":
        chat.handleEvent(event);
        break;
      case "confirm.request":
        chat.handleEvent(event);
        break;
      case "confirm.done":
        chat.handleEvent(event);
        break;
      case "error":
        chat.handleEvent(event);
        setStatus("错误");
        break;
      default:
        chat.handleEvent(event);
        break;
    }
  });

  renderTopbar();
  renderExpand();
  renderChat();
  setComposerEnabled(true);
  setStatus("已连接");

  return () => {
    if (cancelledStatusTimer !== null) window.clearTimeout(cancelledStatusTimer);
    fileDrop.destroy();
    off();
    shell.classList.remove("is-working");
    setAgentBusy(false);
    root.innerHTML = "";
  };
}
