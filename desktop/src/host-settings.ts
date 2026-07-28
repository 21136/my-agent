import type { AgentWsClient, HostScopeRoot } from "./api/ws";
import "./host-settings.css";

export type HostSettingsApi = {
  openSettings: () => void;
};

type PendingConfirm = {
  message: string;
  onConfirm: () => void;
};

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function suggestHostId(folderPath: string): string {
  const base = folderPath.replace(/[/\\]+$/, "").split(/[/\\]/).pop() ?? "folder";
  let slug = base.toLowerCase().replace(/[^a-z0-9_-]+/g, "_").replace(/^_|_$/g, "");
  if (!slug || !/^[a-z][a-z0-9_-]*$/.test(slug)) {
    slug = base.toLowerCase().includes("download") ? "downloads" : "folder";
  }
  return slug;
}

export function mountHostSettings(client: AgentWsClient): HostSettingsApi {
  const overlay = document.createElement("div");
  overlay.className = "host-settings-overlay hidden";
  overlay.innerHTML = `
    <div class="host-settings-panel" role="dialog" aria-labelledby="host-settings-title">
      <header class="host-settings-header">
        <span class="host-settings-title" id="host-settings-title">托管文件夹</span>
        <button type="button" class="host-settings-btn" data-action="close">关闭</button>
      </header>
      <div class="host-settings-body" id="host-settings-body"></div>
      <footer class="host-settings-footer" id="host-settings-footer"></footer>
    </div>
  `;
  document.body.appendChild(overlay);

  const wizardOverlay = document.createElement("div");
  wizardOverlay.className = "host-settings-overlay hidden";
  wizardOverlay.innerHTML = `
    <div class="host-settings-panel" role="dialog" aria-labelledby="host-wizard-title">
      <header class="host-settings-header">
        <span class="host-settings-title" id="host-wizard-title">欢迎使用主机托管区</span>
      </header>
      <div class="host-settings-body" id="host-wizard-body"></div>
      <footer class="host-settings-footer" id="host-wizard-footer"></footer>
    </div>
  `;
  document.body.appendChild(wizardOverlay);

  const body = overlay.querySelector<HTMLElement>("#host-settings-body")!;
  const footer = overlay.querySelector<HTMLElement>("#host-settings-footer")!;
  const wizardBody = wizardOverlay.querySelector<HTMLElement>("#host-wizard-body")!;
  const wizardFooter = wizardOverlay.querySelector<HTMLElement>("#host-wizard-footer")!;

  let roots: HostScopeRoot[] = [];
  let wizardSuggested = false;
  let pendingConfirm: PendingConfirm | null = null;
  let addFormPath: string | null = null;
  let wizardShown = false;
  let wizardPendingConfirm: PendingConfirm | null = null;

  function closeSettings(): void {
    overlay.classList.add("hidden");
    pendingConfirm = null;
    addFormPath = null;
    renderSettings();
  }

  function closeWizard(): void {
    wizardOverlay.classList.add("hidden");
  }

  function showConfirm(message: string, onConfirm: () => void): void {
    pendingConfirm = { message, onConfirm };
    renderSettings();
  }

  function renderConfirmBar(container: HTMLElement): void {
    if (!pendingConfirm) return;
    const bar = document.createElement("div");
    bar.className = "host-confirm-bar";
    bar.innerHTML = `
      <div class="host-confirm-text">${escapeHtml(pendingConfirm.message)}</div>
      <div class="host-confirm-actions">
        <button type="button" class="host-settings-btn host-settings-btn-accent" data-confirm="y">确认</button>
        <button type="button" class="host-settings-btn" data-confirm="n">取消</button>
      </div>
    `;
    bar.querySelector<HTMLButtonElement>('[data-confirm="y"]')?.addEventListener("click", () => {
      const action = pendingConfirm?.onConfirm;
      pendingConfirm = null;
      action?.();
    });
    bar.querySelector<HTMLButtonElement>('[data-confirm="n"]')?.addEventListener("click", () => {
      pendingConfirm = null;
      renderSettings();
    });
    container.append(bar);
  }

  function renderSettings(): void {
    body.innerHTML = "";
    footer.innerHTML = "";

    if (!roots.length) {
      const empty = document.createElement("p");
      empty.className = "host-settings-empty";
      empty.textContent = "尚未登记托管文件夹。添加后可在对话中使用 host:<id>/… 路径。";
      body.append(empty);
    } else {
      const list = document.createElement("ul");
      list.className = "host-settings-list";
      for (const root of roots) {
        const item = document.createElement("li");
        item.className = "host-settings-item";
        item.innerHTML = `
          <div class="host-settings-item-head">
            <span class="host-settings-id">${escapeHtml(root.id)}</span>
            <span class="host-settings-badge">${escapeHtml(root.permissions)}</span>
          </div>
          <div class="host-settings-path">${escapeHtml(root.path)}</div>
          ${
            !root.write
              ? `<p class="host-settings-hint">只读：对话可列出/读取；整理文件请开启写权限。</p>`
              : ""
          }
          <div class="host-settings-actions">
            <button type="button" class="host-settings-btn" data-action="repath" data-id="${escapeHtml(root.id)}">更换文件夹…</button>
            ${
              root.write
                ? `<button type="button" class="host-settings-btn" data-action="write-off" data-id="${escapeHtml(root.id)}">关闭写</button>`
                : `<button type="button" class="host-settings-btn" data-action="write-on" data-id="${escapeHtml(root.id)}">开启写</button>`
            }
            <button type="button" class="host-settings-btn host-settings-btn-danger" data-action="remove" data-id="${escapeHtml(root.id)}">删除</button>
          </div>
        `;
        list.append(item);
      }
      body.append(list);

      list.querySelectorAll<HTMLButtonElement>('[data-action="repath"]').forEach((btn) => {
        btn.addEventListener("click", () => {
          const id = btn.dataset.id;
          if (!id) return;
          void repathRoot(id);
        });
      });

      list.querySelectorAll<HTMLButtonElement>('[data-action="write-on"]').forEach((btn) => {
        btn.addEventListener("click", () => {
          const id = btn.dataset.id;
          const root = roots.find((r) => r.id === id);
          if (!id || !root) return;
          showConfirm(
            `将开启 ${id} 的写权限\n路径: ${root.path}`,
            () => client.setHostScopeWrite(id, true),
          );
        });
      });

      list.querySelectorAll<HTMLButtonElement>('[data-action="write-off"]').forEach((btn) => {
        btn.addEventListener("click", () => {
          const id = btn.dataset.id;
          if (!id) return;
          client.setHostScopeWrite(id, false);
        });
      });

      list.querySelectorAll<HTMLButtonElement>('[data-action="remove"]').forEach((btn) => {
        btn.addEventListener("click", () => {
          const id = btn.dataset.id;
          const root = roots.find((r) => r.id === id);
          if (!id || !root) return;
          showConfirm(
            `将删除托管目录 ${id}\n路径: ${root.path}`,
            () => client.removeHostScope(id),
          );
        });
      });
    }

    if (addFormPath) {
      const form = document.createElement("div");
      form.className = "host-settings-form";
      const suggestedId = suggestHostId(addFormPath);
      form.innerHTML = `
        <div class="host-settings-field">
          <label for="host-add-path">路径</label>
          <input id="host-add-path" type="text" readonly value="${escapeHtml(addFormPath)}" />
        </div>
        <div class="host-settings-field">
          <label for="host-add-id">标识 (host:id)</label>
          <input id="host-add-id" type="text" value="${escapeHtml(suggestedId)}" />
        </div>
        <div class="host-settings-field">
          <label for="host-add-mode">权限</label>
          <select id="host-add-mode">
            <option value="ro">只读</option>
            <option value="rw">读写</option>
          </select>
        </div>
      `;
      body.append(form);

      footer.innerHTML = `
        <button type="button" class="host-settings-btn" data-action="cancel-add">取消</button>
        <button type="button" class="host-settings-btn host-settings-btn-accent" data-action="submit-add">添加</button>
      `;
      footer.querySelector<HTMLButtonElement>('[data-action="cancel-add"]')?.addEventListener("click", () => {
        addFormPath = null;
        renderSettings();
      });
      footer.querySelector<HTMLButtonElement>('[data-action="submit-add"]')?.addEventListener("click", () => {
        const hostId = form.querySelector<HTMLInputElement>("#host-add-id")?.value.trim() ?? "";
        const mode = form.querySelector<HTMLSelectElement>("#host-add-mode")?.value ?? "ro";
        if (!hostId) return;
        const write = mode === "rw";
        showConfirm(
          `将添加托管目录 ${hostId}\n路径: ${addFormPath}\n权限: ${write ? "读写" : "只读"}`,
          () => {
            client.addHostScope({
              host_id: hostId,
              path: addFormPath!,
              label: hostId,
              write,
            });
            addFormPath = null;
          },
        );
      });
    } else if (!pendingConfirm) {
      footer.innerHTML = `<button type="button" class="host-settings-btn host-settings-btn-accent" data-action="pick">添加文件夹…</button>`;
      footer.querySelector<HTMLButtonElement>('[data-action="pick"]')?.addEventListener("click", () => {
        void pickAndStage();
      });
    }

    renderConfirmBar(body);
  }

  async function repathRoot(hostId: string): Promise<void> {
    const api = window.myAgentDesktop;
    if (!api?.pickDirectory) return;
    const picked = await api.pickDirectory();
    if (!picked) return;
    const root = roots.find((r) => r.id === hostId);
    showConfirm(
      `将更换 ${hostId} 的托管路径\n原: ${root?.path ?? "?"}\n新: ${picked}`,
      () => client.repathHostScope(hostId, picked),
    );
  }

  async function pickAndStage(): Promise<void> {
    const api = window.myAgentDesktop;
    if (!api?.pickDirectory) {
      const err = document.createElement("p");
      err.className = "host-settings-error";
      err.textContent = "文件夹选择器不可用（请使用 Electron 桌面壳）。";
      body.prepend(err);
      return;
    }
    const picked = await api.pickDirectory();
    if (!picked) return;
    addFormPath = picked;
    pendingConfirm = null;
    renderSettings();
  }

  async function renderWizard(): Promise<void> {
    const downloads = (await window.myAgentDesktop?.getDownloadsPath?.()) ?? "";
    const desktop = (await window.myAgentDesktop?.getDesktopPath?.()) ?? "";
    wizardBody.innerHTML = `
      <p class="host-wizard-note">
        勾选要托管的文件夹；<strong>读写</strong> 权限下 Agent 可整理、移动文件（每次写操作仍须对话内确认）。
      </p>
      ${
        downloads
          ? `<label class="host-wizard-check">
        <input type="checkbox" id="host-wizard-downloads" checked />
        <span>下载文件夹 <span class="host-settings-path">${escapeHtml(downloads)}</span></span>
      </label>`
          : ""
      }
      ${
        desktop
          ? `<label class="host-wizard-check">
        <input type="checkbox" id="host-wizard-desktop" />
        <span>桌面文件夹 <span class="host-settings-path">${escapeHtml(desktop)}</span></span>
      </label>`
          : ""
      }
      <div class="host-settings-field" style="margin-top:0.75rem">
        <label for="host-wizard-mode">权限</label>
        <select id="host-wizard-mode">
          <option value="ro">只读（浏览、搜索）</option>
          <option value="rw">读写（可整理文件）</option>
        </select>
      </div>
      <div id="host-wizard-confirm-slot"></div>
    `;
    wizardFooter.innerHTML = `
      <button type="button" class="host-settings-btn" data-action="wizard-skip">稍后</button>
      <button type="button" class="host-settings-btn host-settings-btn-accent" data-action="wizard-go">继续</button>
    `;
    wizardPendingConfirm = null;
    renderWizardConfirm();

    wizardFooter.querySelector<HTMLButtonElement>('[data-action="wizard-skip"]')?.addEventListener("click", () => {
      client.skipHostScopeWizard();
      closeWizard();
    });
    wizardFooter.querySelector<HTMLButtonElement>('[data-action="wizard-go"]')?.addEventListener("click", () => {
      void submitWizard();
    });
  }

  function renderWizardConfirm(): void {
    const slot = wizardBody.querySelector<HTMLElement>("#host-wizard-confirm-slot");
    if (!slot) return;
    slot.innerHTML = "";
    if (!wizardPendingConfirm) return;
    const bar = document.createElement("div");
    bar.className = "host-confirm-bar";
    bar.innerHTML = `
      <div class="host-confirm-text">${escapeHtml(wizardPendingConfirm.message)}</div>
      <div class="host-confirm-actions">
        <button type="button" class="host-settings-btn host-settings-btn-accent" data-confirm="y">确认</button>
        <button type="button" class="host-settings-btn" data-confirm="n">取消</button>
      </div>
    `;
    bar.querySelector<HTMLButtonElement>('[data-confirm="y"]')?.addEventListener("click", () => {
      const action = wizardPendingConfirm?.onConfirm;
      wizardPendingConfirm = null;
      action?.();
    });
    bar.querySelector<HTMLButtonElement>('[data-confirm="n"]')?.addEventListener("click", () => {
      wizardPendingConfirm = null;
      renderWizardConfirm();
    });
    slot.append(bar);
  }

  function submitWizard(): void {
    const downloadsChecked = wizardBody.querySelector<HTMLInputElement>("#host-wizard-downloads")?.checked;
    const desktopChecked = wizardBody.querySelector<HTMLInputElement>("#host-wizard-desktop")?.checked;
    const write = wizardBody.querySelector<HTMLSelectElement>("#host-wizard-mode")?.value === "rw";

    void (async () => {
      const downloads = (await window.myAgentDesktop?.getDownloadsPath?.()) ?? "";
      const desktop = (await window.myAgentDesktop?.getDesktopPath?.()) ?? "";
      const entries: Array<{ host_id: string; path: string; label: string; write: boolean }> = [];
      if (downloadsChecked && downloads) {
        entries.push({ host_id: "downloads", path: downloads, label: "Downloads", write });
      }
      if (desktopChecked && desktop) {
        entries.push({ host_id: "desktop", path: desktop, label: "Desktop", write });
      }

      if (!entries.length) {
        client.skipHostScopeWizard();
        closeWizard();
        return;
      }

      const run = () => {
        client.runHostScopeWizard(entries);
        closeWizard();
      };

      if (write) {
        const lines = entries.map((e) => `  ${e.host_id} → ${e.path}（读写）`).join("\n");
        wizardPendingConfirm = {
          message: `将添加以下托管区（读写）：\n${lines}`,
          onConfirm: run,
        };
        renderWizardConfirm();
        return;
      }

      run();
    })();
  }

  function maybeShowWizard(): void {
    if (wizardShown || !wizardSuggested) return;
    wizardShown = true;
    void renderWizard().then(() => {
      wizardOverlay.classList.remove("hidden");
    });
  }

  function applyState(event: { roots: HostScopeRoot[]; wizard_suggested: boolean }): void {
    roots = event.roots;
    wizardSuggested = event.wizard_suggested;
    if (!overlay.classList.contains("hidden")) {
      renderSettings();
    }
    maybeShowWizard();
  }

  const offEvents = client.onEvent((event) => {
    if (event.type === "host_scope.state" || event.type === "host_scope.updated") {
      applyState(event);
    }
    if (event.type === "error" && !overlay.classList.contains("hidden")) {
      const err = document.createElement("p");
      err.className = "host-settings-error";
      err.textContent = event.message;
      body.prepend(err);
    }
  });

  overlay.querySelector<HTMLButtonElement>('[data-action="close"]')?.addEventListener("click", closeSettings);
  overlay.addEventListener("click", (ev) => {
    if (ev.target === overlay) closeSettings();
  });
  wizardOverlay.addEventListener("click", (ev) => {
    if (ev.target === wizardOverlay) {
      client.skipHostScopeWizard();
      closeWizard();
    }
  });

  client.listHostScope();

  window.addEventListener("beforeunload", () => {
    offEvents();
    overlay.remove();
    wizardOverlay.remove();
  });

  return {
    openSettings(): void {
      pendingConfirm = null;
      addFormPath = null;
      renderSettings();
      overlay.classList.remove("hidden");
      client.listHostScope();
    },
  };
}
