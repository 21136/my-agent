import test from "node:test";
import assert from "node:assert/strict";
import { createBrowserDesktopApi, isBrowserBridgeAllowed } from "../src/browser-desktop-bridge.ts";

test("browser bridge requires dev opt-in, explicit URL opt-in, and loopback origin", () => {
  const base = { devBuild: true, href: "http://127.0.0.1:5173/?myAgentBrowserBridge=1" };
  assert.equal(isBrowserBridgeAllowed(base), true);
  assert.equal(isBrowserBridgeAllowed({ ...base, devBuild: false }), false);
  assert.equal(isBrowserBridgeAllowed({ ...base, href: "http://127.0.0.1:5173/" }), false);
  assert.equal(isBrowserBridgeAllowed({ ...base, href: "http://192.168.1.2:5173/?myAgentBrowserBridge=1" }), false);
  assert.equal(isBrowserBridgeAllowed({ ...base, href: "https://127.0.0.1:5173/?myAgentBrowserBridge=1" }), false);
  assert.equal(isBrowserBridgeAllowed({ ...base, href: "file:///index.html?myAgentBrowserBridge=1" }), false);
});

test("bridge exposes only real loopback sidecar and denies desktop-only capabilities", async () => {
  const api = createBrowserDesktopApi({ host: "127.0.0.1", port: 8765 });
  assert.deepEqual(await api.getSidecar(), { host: "127.0.0.1", port: 8765 });
  assert.deepEqual(await api.getSessionLaneCapabilities(), { legacySessionLanes: false });
  assert.equal(await api.pickDirectory(), null);
  assert.equal(await api.openExternal("https://example.test"), false);
  await assert.rejects(api.switchToCli(), /Electron/);
});

test("constellation bridge persists renderer-owned data without filesystem access", async () => {
  const values = new Map<string, string>();
  const storage = {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value); },
    removeItem: (key: string) => { values.delete(key); },
  };
  const api = createBrowserDesktopApi({ host: "127.0.0.1", port: 8765, storage });
  const payload = { version: 1 as const, stars: [{ id: "real-ui" }], links: [] };
  assert.equal(await api.writeConstellation(payload), true);
  assert.deepEqual(await api.readConstellation(), payload);
  assert.equal(await api.clearConstellation(), true);
  assert.deepEqual(await api.readConstellation(), { version: 1, stars: [], links: [] });
});
