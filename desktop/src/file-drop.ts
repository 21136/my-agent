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
  image_input?: boolean;
};

const IMAGE_MIMES = new Set([
  "image/png",
  "image/jpeg",
  "image/webp",
  "image/gif",
]);

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

function extForMime(mime: string): string {
  switch (mime.trim().toLowerCase()) {
    case "image/jpeg":
      return ".jpg";
    case "image/webp":
      return ".webp";
    case "image/gif":
      return ".gif";
    default:
      return ".png";
  }
}

async function collectPastePaths(dataTransfer: DataTransfer): Promise<string[]> {
  const api = window.myAgentDesktop;
  const paths: string[] = [];
  const seen = new Set<string>();
  const push = (value: string) => {
    const text = value.trim();
    if (!text || seen.has(text)) return;
    seen.add(text);
    paths.push(text);
  };

  if (api?.getPathForFile) {
    for (const file of Array.from(dataTransfer.files)) {
      push(api.getPathForFile(file));
    }
  }

  let sawImageItem = false;
  for (const item of Array.from(dataTransfer.items)) {
    const mime = (item.type || "").trim().toLowerCase();
    if (item.kind !== "file" && !mime.startsWith("image/")) continue;
    const file = item.getAsFile();
    if (!file) continue;
    if (mime.startsWith("image/") || IMAGE_MIMES.has(file.type)) {
      sawImageItem = true;
    }

    if (api?.getPathForFile) {
      const fromPath = api.getPathForFile(file);
      if (fromPath) {
        push(fromPath);
        continue;
      }
    }

    const fileMime = (file.type || mime || "image/png").toLowerCase();
    if (!fileMime.startsWith("image/") && !IMAGE_MIMES.has(fileMime)) continue;
    if (!api?.writeTempStagingFile) continue;

    const bytes = new Uint8Array(await file.arrayBuffer());
    if (!bytes.length) continue;
    const ext = extForMime(fileMime);
    const name = file.name?.trim() || `paste${ext}`;
    try {
      push(await api.writeTempStagingFile(name, bytes));
    } catch {
      // fall through to clipboard fallback
    }
  }

  if (!paths.length && sawImageItem && api?.readClipboardImageToTemp) {
    try {
      const fromClipboard = await api.readClipboardImageToTemp();
      if (fromClipboard) push(fromClipboard);
    } catch {
      // ignore
    }
  }

  return paths;
}

export type FileDropHandle = {
  getAttachments: () => StagedFile[];
  clearAttachments: () => void;
  destroy: () => void;
};

export function mountFileDrop(options: {
  composer: HTMLElement;
  pasteTargets?: HTMLElement[];
  client: AgentWsClient;
  shell: ShellId;
  canAccept: () => boolean;
  onChange: (items: StagedFile[]) => void;
  onNotice?: (text: string) => void;
}): FileDropHandle {
  const { composer, client, shell, canAccept, onChange, onNotice } = options;
  const pasteTargets = options.pasteTargets?.length
    ? options.pasteTargets
    : [composer];
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
      const readable = item.image_input ? " · 可识图" : item.readable_text ? "" : " · 非文本";
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

  async function stageAbsolutePaths(paths: string[]): Promise<void> {
    if (!paths.length) return;
    try {
      client.stageFiles(paths, shell);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      onNotice?.(message);
    }
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
      const filePath = api.getPathForFile(file);
      if (filePath) paths.push(filePath);
    }
    if (!paths.length) {
      onNotice?.("无法读取拖入文件的路径");
      return;
    }

    await stageAbsolutePaths(paths);
  }

  async function onPaste(ev: ClipboardEvent): Promise<void> {
    if (!canAccept()) return;
    const dataTransfer = ev.clipboardData;
    if (!dataTransfer) return;

    const hasPasteable = dataTransfer.types.some((type) => type === "Files" || type.startsWith("image/"))
      || Array.from(dataTransfer.items).some(
        (item) => item.kind === "file" || item.type.startsWith("image/"),
      );
    if (!hasPasteable) return;

    const paths = await collectPastePaths(dataTransfer);
    if (!paths.length) {
      onNotice?.("无法读取剪贴板图片，请保存为文件后拖入");
      return;
    }

    ev.preventDefault();
    await stageAbsolutePaths(paths);
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
  for (const target of pasteTargets) {
    target.addEventListener("paste", onPaste);
  }

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
      for (const target of pasteTargets) {
        target.removeEventListener("paste", onPaste);
      }
      chips.remove();
      composer.classList.remove("file-drop-active");
    },
  };
}
