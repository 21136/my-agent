export type ManagedSessionMode = "read_only" | "fenced_application";

export interface ManagedSessionBridge {
  health(): Promise<unknown>;
  listSessions(): Promise<unknown>;
  createSession(input: { sessionId: string; title: string }): Promise<unknown>;
  readSession(sessionId: string): Promise<unknown>;
  appendDocument(input: { sessionId: string; text: string }): Promise<unknown>;
  getMode(): Promise<ManagedSessionMode | { mode?: unknown; access?: unknown } | unknown>;
}

declare global {
  interface Window {
    myAgentManaged?: ManagedSessionBridge;
  }
}

type RecordValue = Record<string, unknown>;
type ManagedSession = { session_id: string; title: string };

function record(value: unknown): value is RecordValue {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function unwrap(value: unknown): RecordValue {
  if (!record(value)) throw new Error("请求失败");
  if (value.type === "error" || value.error) throw new Error("请求失败");
  return value;
}

function modeFrom(value: unknown): ManagedSessionMode {
  const mode = record(value) ? value.mode ?? value.access : value;
  return mode === "fenced_application" ? "fenced_application" : "read_only";
}

function sessionsFrom(value: unknown): ManagedSession[] {
  const payload = Array.isArray(value) ? {} : unwrap(value);
  const rows = Array.isArray(value) ? value : payload.sessions;
  if (!Array.isArray(rows)) throw new Error("读取记录失败");
  return rows.flatMap((item): ManagedSession[] => {
    if (!record(item)) return [];
    const sessionId = typeof item.session_id === "string" ? item.session_id : typeof item.sessionId === "string" ? item.sessionId : "";
    const title = typeof item.title === "string" && item.title.trim() ? item.title : "未命名记录";
    return sessionId ? [{ session_id: sessionId, title }] : [];
  });
}

function safeIdentity(value: unknown): string[] {
  if (!record(value)) return [];
  const fields: Array<[string, string]> = [
    ["installation_id", "本地空间"], ["data_store_id", "数据集"], ["release_id", "版本"],
  ];
  return fields.flatMap(([key, label]) => {
    const item = value[key];
    return typeof item === "string" && /^[a-zA-Z0-9._-]{1,96}$/.test(item) ? [`${label}：${item}`] : [];
  });
}

export async function mountManagedSessionView(
  root: HTMLElement,
  bridge: ManagedSessionBridge,
  doc: Document = document,
): Promise<void> {
  root.replaceChildren();
  root.dataset.managedView = "loading";

  const frame = doc.createElement("div");
  frame.className = "app-frame";
  const main = doc.createElement("main");
  main.className = "shell-host shell-placeholder";
  main.style.gap = "1rem";
  main.style.overflow = "auto";

  const heading = doc.createElement("h1");
  heading.textContent = "本地记录";
  const identity = doc.createElement("p");
  identity.className = "text-muted";
  const modeLabel = doc.createElement("p");
  modeLabel.className = "text-muted";
  const status = doc.createElement("p");
  status.className = "text-muted";
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");

  const createLabel = doc.createElement("label");
  createLabel.textContent = "记录名称";
  const titleInput = doc.createElement("input");
  titleInput.type = "text";
  titleInput.maxLength = 120;
  titleInput.dataset.managedField = "title";
  titleInput.setAttribute("aria-label", "记录名称");
  const createButton = doc.createElement("button");
  createButton.type = "button";
  createButton.dataset.managedAction = "create";
  createButton.textContent = "新建记录";
  createButton.disabled = true;
  createLabel.append(titleInput);
  const createRow = doc.createElement("div");
  createRow.append(createLabel, createButton);

  const listHeading = doc.createElement("h2");
  listHeading.textContent = "记录列表";
  const list = doc.createElement("nav");
  list.setAttribute("aria-label", "本地记录");
  const contentHeading = doc.createElement("h2");
  contentHeading.textContent = "记录内容";
  const documents = doc.createElement("section");
  documents.setAttribute("aria-label", "记录内容");
  const editorLabel = doc.createElement("label");
  editorLabel.textContent = "追加一段文字";
  const editor = doc.createElement("textarea");
  editor.dataset.managedField = "document";
  editor.rows = 6;
  editor.setAttribute("aria-label", "追加一段文字");
  editor.disabled = true;
  const saveButton = doc.createElement("button");
  saveButton.type = "button";
  saveButton.dataset.managedAction = "save";
  saveButton.textContent = "保存记录";
  saveButton.disabled = true;
  editorLabel.append(editor);

  main.append(heading, identity, modeLabel, status, createRow, listHeading, list, contentHeading, documents, editorLabel, saveButton);
  frame.append(main);
  root.append(frame);

  let currentSessionId: string | null = null;
  let selectionRevision = 0;
  let readRevision = 0;
  let canWrite = false;
  let sessions: ManagedSession[] = [];

  const setStatus = (message: string) => { status.textContent = message; };
  const renderSessions = () => {
    list.replaceChildren();
    if (!sessions.length) {
      const empty = doc.createElement("p");
      empty.className = "text-muted";
      empty.textContent = "还没有记录";
      list.append(empty);
      return;
    }
    for (const session of sessions) {
      const button = doc.createElement("button");
      button.type = "button";
      button.dataset.managedSession = session.session_id;
      button.textContent = session.title;
      button.setAttribute("aria-current", String(session.session_id === currentSessionId));
      button.addEventListener("click", async () => { await selectSession(session.session_id); });
      list.append(button);
    }
  };

  const readSelectedSession = async (sessionId: string, selectedAtStart: number): Promise<boolean> => {
    const request = ++readRevision;
    try {
      const payload = unwrap(await bridge.readSession(sessionId));
      if (request !== readRevision || selectedAtStart !== selectionRevision || currentSessionId !== sessionId) return false;
      const session = record(payload.session) ? payload.session : payload;
      const entries = Array.isArray(session.documents) ? session.documents : [];
      documents.replaceChildren();
      if (!entries.length) {
        const empty = doc.createElement("p");
        empty.className = "text-muted";
        empty.textContent = "这条记录还没有内容";
        documents.append(empty);
      }
      for (const entry of entries) {
        if (!record(entry) || typeof entry.text !== "string") continue;
        const paragraph = doc.createElement("p");
        paragraph.textContent = entry.text;
        documents.append(paragraph);
      }
      setStatus("");
      return true;
    } catch {
      if (request === readRevision && selectedAtStart === selectionRevision && currentSessionId === sessionId) {
        setStatus("暂时无法读取这条记录，请重试。");
      }
      return false;
    }
  };

  const selectSession = async (sessionId: string) => {
    currentSessionId = sessionId;
    const selectedAtStart = ++selectionRevision;
    renderSessions();
    editor.value = "";
    saveButton.disabled = !canWrite;
    documents.replaceChildren();
    await readSelectedSession(sessionId, selectedAtStart);
  };

  createButton.addEventListener("click", async () => {
    if (!canWrite || createButton.disabled) return;
    const title = titleInput.value.trim();
    if (!title) { setStatus("请先填写记录名称。"); return; }
    const selectedAtStart = selectionRevision;
    createButton.disabled = true;
    try {
      const sessionId = globalThis.crypto.randomUUID();
      unwrap(await bridge.createSession({ sessionId, title }));
      sessions = sessionsFrom(await bridge.listSessions());
      titleInput.value = "";
      renderSessions();
      if (selectedAtStart === selectionRevision) {
        await selectSession(sessionId);
        if (currentSessionId === sessionId) setStatus("记录已创建。");
      }
    } catch {
      if (selectedAtStart === selectionRevision) setStatus("暂时无法新建记录，请重试。");
    } finally {
      createButton.disabled = !canWrite;
    }
  });

  saveButton.addEventListener("click", async () => {
    if (!canWrite || !currentSessionId || saveButton.disabled) return;
    const sessionId = currentSessionId;
    const text = editor.value.trim();
    if (!text) { setStatus("请先填写要保存的文字。"); return; }
    saveButton.disabled = true;
    try {
      unwrap(await bridge.appendDocument({ sessionId, text }));
      if (currentSessionId === sessionId) {
        const selectedAtStart = selectionRevision;
        const refreshed = await readSelectedSession(sessionId, selectedAtStart);
        if (refreshed && currentSessionId === sessionId && selectionRevision === selectedAtStart) {
          if (editor.value.trim() === text) editor.value = "";
          setStatus("记录已保存。");
        }
      }
    } catch {
      if (currentSessionId === sessionId) setStatus("暂时无法保存记录，请重试。");
    } finally {
      saveButton.disabled = !canWrite || !currentSessionId;
    }
  });

  try {
    const health = unwrap(await bridge.health());
    if (health.ready !== true) throw new Error("Host unavailable");
    identity.textContent = safeIdentity(health).join("　");
    const mode = modeFrom(await bridge.getMode());
    canWrite = mode === "fenced_application";
    modeLabel.textContent = canWrite ? "可保存本地记录" : "只读检查";
    titleInput.disabled = !canWrite;
    editor.disabled = !canWrite;
    createButton.disabled = !canWrite;
    sessions = sessionsFrom(await bridge.listSessions());
    renderSessions();
    root.dataset.managedView = "ready";
    setStatus("");
  } catch {
    root.dataset.managedView = "error";
    titleInput.disabled = true;
    createButton.disabled = true;
    editor.disabled = true;
    saveButton.disabled = true;
    setStatus("本地记录暂时无法打开，请稍后重试。");
  }
}
