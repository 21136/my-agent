import type { AgentWsClient, ShellId } from "./api/ws";
import "./file-drop.css";

export type StagedFile = {
  id: string;
  name: string;
  ref: string;
  size: number;
  mime: string;
  readable_text: boolean;
  copied: boolean;
};

function formatSize(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export type FileDropHandle = {
  getAttachments: () => StagedFile[];
  clearAttachments: () => void;
  destroy: () => void;
};

export function mountFileDrop(options: {
  composer: HTMLElement;
  client: AgentWsClient;
  shell: ShellId;
  canAccept: () => boolean;
  onChange: (items: StagedFile[]) => void;
  onNotice?: (text: string) => void;
}): FileDropHandle {
  const { composer, client, shell, canAccept, onChange, onNotice } = options;
  let items: StagedFile[] = [];

  const chips = document.createElement("div");
  chips.className = "file-drop-chips hidden";
  composer.prepend(chips);

  function renderChips(): void {
    chips.innerHTML = "";
    if (!items.length) {
      chips.classList.add("hidden");
      return;
    }
    chips.classList.remove("hidden");
    for (const item of items) {
      const chip = document.createElement("span");
      chip.className = "file-drop-chip";
      const readable = item.readable_text ? "" : " · 非文本";
      chip.innerHTML = `
        <span class="file-drop-chip-name" title="${escapeHtml(item.ref)}">${escapeHtml(item.name)}</span>
        <span class="file-drop-chip-meta">${formatSize(item.size)}${readable}</span>
        <button type="button" class="file-drop-chip-remove" aria-label="移除">×</button>
      `;
      chip.querySelector<HTMLButtonElement>(".file-drop-chip-remove")!.addEventListener("click", () => {
        void removeItem(item.id);
      });
      chips.appendChild(chip);
    }
  }

  function setItems(next: StagedFile[]): void {
    items = next;
    renderChips();
    onChange(items);
  }

  async function removeItem(id: string): Promise<void> {
    try {
      client.unstageFile(id);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      onNotice?.(message);
    }
    setItems(items.filter((item) => item.id !== id));
  }

  function onDragOver(ev: DragEvent): void {
    if (!canAccept()) return;
    if (!ev.dataTransfer?.types.includes("Files")) return;
    ev.preventDefault();
    ev.dataTransfer.dropEffect = "copy";
    composer.classList.add("file-drop-active");
  }

  function onDragLeave(ev: DragEvent): void {
    if (ev.currentTarget === ev.target) {
      composer.classList.remove("file-drop-active");
    }
  }

  async function onDrop(ev: DragEvent): Promise<void> {
    ev.preventDefault();
    composer.classList.remove("file-drop-active");
    if (!canAccept()) return;

    const api = window.myAgentDesktop;
    if (!api?.getPathForFile) {
      onNotice?.("当前环境不支持文件拖放");
      return;
    }

    const files = Array.from(ev.dataTransfer?.files ?? []);
    if (!files.length) return;

    const paths: string[] = [];
    for (const file of files) {
      const path = api.getPathForFile(file);
      if (path) paths.push(path);
    }
    if (!paths.length) {
      onNotice?.("无法读取拖入文件的路径");
      return;
    }

    try {
      client.stageFiles(paths, shell);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      onNotice?.(message);
    }
  }

  const offStaged = client.onFileStaged((staged) => {
    const map = new Map(items.map((item) => [item.id, item]));
    for (const item of staged) {
      map.set(item.id, item);
    }
    setItems(Array.from(map.values()));
  });

  const offError = client.onFileError((event) => {
    onNotice?.(event.path ? `${event.message}（${event.path}）` : event.message);
  });

  const offUnstaged = client.onFileUnstaged((attachmentId) => {
    setItems(items.filter((item) => item.id !== attachmentId));
  });

  composer.addEventListener("dragover", onDragOver);
  composer.addEventListener("dragleave", onDragLeave);
  composer.addEventListener("drop", onDrop);

  return {
    getAttachments: () => [...items],
    clearAttachments: () => setItems([]),
    destroy: () => {
      offStaged();
      offError();
      offUnstaged();
      composer.removeEventListener("dragover", onDragOver);
      composer.removeEventListener("dragleave", onDragLeave);
      composer.removeEventListener("drop", onDrop);
      chips.remove();
      composer.classList.remove("file-drop-active");
    },
  };
}
