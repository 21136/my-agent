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

  const frame = doc.createElement("main");
  frame.className = "managed-workspace";
  frame.dataset.viewState = "loading";
  const sidebar = doc.createElement("aside");
  sidebar.className = "managed-sidebar";
  sidebar.setAttribute("aria-label", "记录导航");
  const brand = doc.createElement("div");
  brand.className = "managed-brand";
  const brandMark = doc.createElement("span");
  brandMark.className = "managed-brand-mark";
  brandMark.textContent = "m";
  brandMark.setAttribute("aria-hidden", "true");
  const brandText = doc.createElement("div");
  const brandName = doc.createElement("strong");
  brandName.className = "managed-brand-title";
  brandName.textContent = "my-agent";
  const brandSubtitle = doc.createElement("span");
  brandSubtitle.className = "managed-brand-subtitle";
  brandSubtitle.textContent = "本地工作空间";
  brandText.append(brandName, brandSubtitle);
  brand.append(brandMark, brandText);
  const main = doc.createElement("section");
  main.className = "managed-main";
  main.setAttribute("aria-label", "记录工作区");
  const toolbar = doc.createElement("header");
  toolbar.className = "managed-toolbar";
  const breadcrumb = doc.createElement("span");
  breadcrumb.className = "managed-breadcrumb";
  breadcrumb.textContent = "工作空间 / 本地记录";
  toolbar.append(breadcrumb);
  const contentHeader = doc.createElement("div");
  contentHeader.className = "managed-content-header";
  const heading = doc.createElement("h1");
  heading.textContent = "留住每一个好想法";
  const contentHint = doc.createElement("p");
  contentHint.textContent = "选择左侧记录继续，或新建一条记录。";
  contentHeader.append(heading, contentHint);
  const identity = doc.createElement("p");
  identity.className = "managed-identity";
  const details = doc.createElement("details");
  details.className = "managed-details";
  const detailsSummary = doc.createElement("summary");
  detailsSummary.textContent = "运行信息";
  details.append(detailsSummary, identity);
  const modeLabel = doc.createElement("p");
  modeLabel.className = "managed-mode";
  modeLabel.textContent = "正在连接本地空间";
  const status = doc.createElement("p");
  status.className = "managed-status";
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");

  const createLabel = doc.createElement("label");
  createLabel.textContent = "记录名称";
  const titleInput = doc.createElement("input");
  titleInput.type = "text";
  titleInput.maxLength = 120;
  titleInput.placeholder = "给记录起个名字";
  titleInput.dataset.managedField = "title";
  titleInput.setAttribute("aria-label", "记录名称");
  const createButton = doc.createElement("button");
  createButton.type = "button";
  createButton.dataset.managedAction = "create";
  createButton.textContent = "新建记录";
  createButton.className = "managed-primary-button";
  createButton.disabled = true;
  createLabel.append(titleInput);
  const createRow = doc.createElement("div");
  createRow.className = "managed-create-form";
  createRow.append(createLabel, createButton);

  const listHeading = doc.createElement("div");
  listHeading.className = "managed-section-heading";
  const listTitle = doc.createElement("span");
  listTitle.className = "managed-section-label";
  listTitle.textContent = "我的记录";
  const recordCount = doc.createElement("span");
  recordCount.className = "managed-record-count";
  recordCount.textContent = "0";
  listHeading.append(listTitle, recordCount);
  const list = doc.createElement("nav");
  list.className = "managed-record-list";
  list.setAttribute("aria-label", "本地记录");
  const documents = doc.createElement("section");
  documents.className = "managed-documents";
  documents.setAttribute("aria-label", "记录内容");
  const editorLabel = doc.createElement("label");
  editorLabel.textContent = "追加一段文字";
  const editor = doc.createElement("textarea");
  editor.dataset.managedField = "document";
  editor.rows = 6;
  editor.placeholder = "在这里写下想法、进展，或下一步计划…";
  editor.setAttribute("aria-label", "追加一段文字");
  editor.disabled = true;
  const saveButton = doc.createElement("button");
  saveButton.type = "button";
  saveButton.dataset.managedAction = "save";
  saveButton.textContent = "保存记录";
  saveButton.className = "managed-primary-button";
  saveButton.disabled = true;
  editorLabel.append(editor);

  const composer = doc.createElement("div");
  composer.className = "managed-composer";
  const composerFooter = doc.createElement("div");
  composerFooter.className = "managed-composer-footer";
  const saveHint = doc.createElement("span");
  saveHint.className = "managed-composer-hint";
  saveHint.textContent = "选择记录后可追加内容";
  composerFooter.append(saveHint, saveButton);
  composer.append(editorLabel, composerFooter);
  const sidebarFooter = doc.createElement("footer");
  sidebarFooter.className = "managed-sidebar-footer";
  sidebarFooter.append(modeLabel, details);
  sidebar.append(brand, createRow, listHeading, list, sidebarFooter);
  main.append(toolbar, contentHeader, documents, composer, status);
  frame.append(sidebar, main);
  root.append(frame);

  const renderEmpty = (title: string, description: string) => {
    const empty = doc.createElement("div");
    empty.className = "managed-empty";
    const icon = doc.createElement("span");
    icon.className = "managed-empty-icon";
    icon.setAttribute("aria-hidden", "true");
    const emptyTitle = doc.createElement("h2");
    emptyTitle.className = "managed-empty-title";
    emptyTitle.textContent = title;
    const hint = doc.createElement("p");
    hint.className = "managed-empty-description";
    hint.textContent = description;
    empty.append(icon, emptyTitle, hint);
    documents.replaceChildren(empty);
  };
  renderEmpty("从一条记录开始", "把灵感与进展留在这里，下次打开时继续。" );

  let currentSessionId: string | null = null;
  let selectionRevision = 0;
  let readRevision = 0;
  let canWrite = false;
  let sessions: ManagedSession[] = [];

  const setStatus = (message: string) => { status.textContent = message; };
  const renderSessions = () => {
    list.replaceChildren();
    recordCount.textContent = String(sessions.length);
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
      button.className = "managed-record-item";
      const icon = doc.createElement("span");
      icon.className = "managed-record-icon";
      icon.setAttribute("aria-hidden", "true");
      const name = doc.createElement("span");
      name.className = "managed-record-name";
      name.textContent = session.title;
      button.append(icon, name);
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
        renderEmpty("这条记录还没有内容", canWrite ? "在下方写下第一段内容，点击保存即可。" : "当前为只读模式，可查看已有记录。" );
      }
      for (const entry of entries) {
        if (!record(entry) || typeof entry.text !== "string") continue;
        const paragraph = doc.createElement("p");
        paragraph.className = "managed-document";
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
    heading.textContent = sessions.find(session => session.session_id === sessionId)?.title ?? "本地记录";
    contentHint.textContent = "你的内容保存在本地，随时回来继续。";
    saveHint.textContent = canWrite ? "保存后将追加到当前记录" : "当前只读，内容不会被修改";
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
    identity.textContent = safeIdentity(health).join("\n") || "本地连接已建立";
    const mode = modeFrom(await bridge.getMode());
    canWrite = mode === "fenced_application";
    modeLabel.textContent = canWrite ? "本地空间 · 可保存" : "本地空间 · 只读";
    modeLabel.dataset.mode = canWrite ? "writable" : "readonly";
    if (!canWrite) {
      saveHint.textContent = "只读模式，内容不会被修改";
      editor.placeholder = "当前为只读模式，可在左侧选择记录查看。";
    }
    titleInput.disabled = !canWrite;
    editor.disabled = !canWrite;
    createButton.disabled = !canWrite;
    sessions = sessionsFrom(await bridge.listSessions());
    renderSessions();
    root.dataset.managedView = "ready";
    frame.dataset.viewState = "ready";
    setStatus("");
  } catch {
    root.dataset.managedView = "error";
    frame.dataset.viewState = "error";
    modeLabel.textContent = "本地空间暂时不可用";
    modeLabel.dataset.mode = "readonly";
    renderEmpty("暂时无法连接本地空间", "请稍后重新打开窗口，你的已有记录不会被修改。" );
    titleInput.disabled = true;
    createButton.disabled = true;
    editor.disabled = true;
    saveButton.disabled = true;
    setStatus("本地记录暂时无法打开，请稍后重试。");
  }
}
