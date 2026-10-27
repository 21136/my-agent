import assert from "node:assert/strict";
import test from "node:test";

import { mountManagedSessionView, type ManagedSessionBridge } from "../src/managed-session-view.ts";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

class FakeElement {
  readonly children: FakeElement[] = [];
  readonly dataset: Record<string, string> = {};
  readonly listeners = new Map<string, Array<(event: { preventDefault(): void }) => unknown>>();
  readonly attributes: Record<string, string> = {};
  readonly style: Record<string, string> = {};
  className = "";
  value = "";
  disabled = false;
  type = "";
  placeholder = "";
  readonly tagName: string;
  private ownText = "";
  constructor(tagName: string) { this.tagName = tagName; }
  set textContent(value: string | null) { this.ownText = value ?? ""; this.children.length = 0; }
  get textContent(): string { return this.ownText + this.children.map((child) => child.textContent).join(""); }
  set innerHTML(_value: string) { throw new Error("HTML injection is forbidden"); }
  append(...children: FakeElement[]): void { this.children.push(...children); }
  replaceChildren(...children: FakeElement[]): void { this.children.splice(0, this.children.length, ...children); }
  setAttribute(name: string, value: string): void { this.attributes[name] = value; }
  addEventListener(type: string, listener: (event: { preventDefault(): void }) => unknown): void {
    const values = this.listeners.get(type) ?? [];
    values.push(listener);
    this.listeners.set(type, values);
  }
  querySelector(selector: string): FakeElement | null {
    const match = selector.match(/^\[data-managed-(action|session|field)(?:="([^"]+)")?\]$/);
    const key = match?.[1];
    const expected = match?.[2];
    for (const child of this.children) {
      const datasetKey = key ? `managed${key[0].toUpperCase()}${key.slice(1)}` : "";
      if (key && datasetKey in child.dataset && (expected === undefined || child.dataset[datasetKey] === expected)) return child;
      const nested = child.querySelector(selector);
      if (nested) return nested;
    }
    return null;
  }
  async click(): Promise<void> {
    for (const listener of this.listeners.get("click") ?? []) await listener({ preventDefault() {} });
  }
}

test("managed session view renders, creates, saves, rereads, and fails closed in readonly mode", async () => {
  const root = new FakeElement("main");
  const sessions = new Map<string, { session_id: string; title: string; documents: Array<{ text: string }> }>();
  const bridge: ManagedSessionBridge = {
    async health() { return { ready: true, installation_id: "install-safe", profile_path: "C:/private/profile", control_token: "never-render" }; },
    async getMode() { return "fenced_application"; },
    async listSessions() { return { sessions: [...sessions.values()].map(({ documents: _documents, ...item }) => item) }; },
    async createSession({ sessionId, title }) {
      sessions.set(sessionId, { session_id: sessionId, title, documents: [] });
      return { type: "session.created" };
    },
    async readSession(sessionId) { return { session: sessions.get(sessionId) }; },
    async appendDocument({ sessionId, text }) {
      sessions.get(sessionId)!.documents.push({ text });
      return { type: "session.appended" };
    },
  };
  const document = { createElement: (tag: string) => new FakeElement(tag) } as unknown as Document;

  await mountManagedSessionView(root as unknown as HTMLElement, bridge, document);
  assert.equal(root.dataset.managedView, "ready");
  assert.ok(root.textContent.includes("install-safe"));
  assert.ok(!root.textContent.includes("C:/private/profile"));
  assert.ok(!root.textContent.includes("never-render"));
  assert.equal(root.querySelector('[data-managed-field="document"]')!.disabled, false);
  const title = root.querySelector('[data-managed-field="title"]')!;
  title.value = "我的记录";
  await root.querySelector('[data-managed-action="create"]')!.click();
  const session = root.querySelector("[data-managed-session]")!;
  assert.equal(session.textContent, "我的记录");
  await session.click();

  const editor = root.querySelector('[data-managed-field="document"]')!;
  editor.value = "<img src=x onerror=alert(1)>";
  await root.querySelector('[data-managed-action="save"]')!.click();
  await session.click();
  assert.ok(root.textContent.includes("<img src=x onerror=alert(1)>"));

  const readonlyRoot = new FakeElement("main");
  await mountManagedSessionView(readonlyRoot as unknown as HTMLElement, { ...bridge, async getMode() { return "read_only"; } }, document);
  assert.equal(readonlyRoot.querySelector('[data-managed-action="create"]')!.disabled, true);
  assert.equal(readonlyRoot.querySelector('[data-managed-action="save"]')!.disabled, true);
  assert.equal(readonlyRoot.querySelector('[data-managed-field="document"]')!.disabled, true);
});

test("an older session read cannot replace the most recently selected session", async () => {
  const root = new FakeElement("main");
  const readA = deferred<unknown>();
  const readB = deferred<unknown>();
  const bridge: ManagedSessionBridge = {
    async health() { return { ready: true }; },
    async getMode() { return "read_only"; },
    async listSessions() { return { sessions: [{ session_id: "a", title: "A" }, { session_id: "b", title: "B" }] }; },
    async createSession() { throw new Error("not used"); },
    readSession(sessionId) { return sessionId === "a" ? readA.promise : readB.promise; },
    async appendDocument() { throw new Error("not used"); },
  };
  const document = { createElement: (tag: string) => new FakeElement(tag) } as unknown as Document;

  await mountManagedSessionView(root as unknown as HTMLElement, bridge, document);
  const clickA = root.querySelector('[data-managed-session="a"]')!.click();
  const clickB = root.querySelector('[data-managed-session="b"]')!.click();
  readB.resolve({ session: { documents: [{ text: "内容B" }] } });
  await clickB;
  readA.resolve({ session: { documents: [{ text: "过期的内容A" }] } });
  await clickA;

  assert.ok(root.textContent.includes("内容B"));
  assert.ok(!root.textContent.includes("过期的内容A"));
  assert.equal(root.querySelector('[data-managed-session="b"]')!.attributes["aria-current"], "true");
});

test("a pending create does not steal a newer session selection", async () => {
  const root = new FakeElement("main");
  const created = deferred<unknown>();
  const sessions = [{ session_id: "b", title: "B" }];
  const bridge: ManagedSessionBridge = {
    async health() { return { ready: true }; },
    async getMode() { return "fenced_application"; },
    async listSessions() { return { sessions }; },
    async createSession({ sessionId, title }) {
      await created.promise;
      sessions.push({ session_id: sessionId, title });
      return { type: "session.created" };
    },
    async readSession(sessionId) { return { session: { documents: [{ text: `内容${sessionId}` }] } }; },
    async appendDocument() { throw new Error("not used"); },
  };
  const document = { createElement: (tag: string) => new FakeElement(tag) } as unknown as Document;

  await mountManagedSessionView(root as unknown as HTMLElement, bridge, document);
  root.querySelector('[data-managed-field="title"]')!.value = "新记录";
  const createClick = root.querySelector('[data-managed-action="create"]')!.click();
  await root.querySelector('[data-managed-session="b"]')!.click();
  created.resolve({ type: "session.created" });
  await createClick;

  assert.ok(root.textContent.includes("内容b"));
  assert.ok(root.querySelector('[data-managed-session="b"]'));
  assert.equal(root.querySelector('[data-managed-session="b"]')!.attributes["aria-current"], "true");
});

test("the append reread supersedes an older read for the same selected session", async () => {
  const root = new FakeElement("main");
  const oldRead = deferred<unknown>();
  const documents = [{ text: "旧快照" }];
  let reads = 0;
  const bridge: ManagedSessionBridge = {
    async health() { return { ready: true }; },
    async getMode() { return "fenced_application"; },
    async listSessions() { return { sessions: [{ session_id: "a", title: "A" }] }; },
    async createSession() { throw new Error("not used"); },
    async readSession() {
      reads += 1;
      return reads === 1 ? oldRead.promise : { session: { documents: [...documents] } };
    },
    async appendDocument({ text }) { documents.push({ text }); return { type: "session.appended" }; },
  };
  const document = { createElement: (tag: string) => new FakeElement(tag) } as unknown as Document;

  await mountManagedSessionView(root as unknown as HTMLElement, bridge, document);
  const selectA = root.querySelector('[data-managed-session="a"]')!.click();
  root.querySelector('[data-managed-field="document"]')!.value = "新保存内容";
  await root.querySelector('[data-managed-action="save"]')!.click();
  oldRead.resolve({ session: { documents: [{ text: "过期响应" }] } });
  await selectA;

  assert.ok(root.textContent.includes("新保存内容"));
  assert.ok(!root.textContent.includes("过期响应"));
});
