import assert from "node:assert/strict";
import { test } from "node:test";
import { installWorkbenchSingleInstance } from "../electron/workbench-single-instance.mjs";

function fakeApp(lockGranted = true) {
  const listeners = new Map();
  const calls = [];
  return {
    calls,
    listeners,
    requestSingleInstanceLock: (additionalData) => { calls.push(["lock", additionalData]); return lockGranted; },
    on: (event, listener) => { calls.push(["on", event]); listeners.set(event, listener); },
  };
}

test("normal duplicate requests one lock and queues activation until the first window exists", () => {
  const app = fakeApp();
  let window = null;
  let activations = 0;
  const instance = installWorkbenchSingleInstance({
    app,
    profile: { kind: "normal", userDataPath: "C:/Users/test/AppData/my-agent" },
    getWindow: () => window,
    activateWindow: (current) => { assert.equal(current, window); activations += 1; },
  });

  assert.equal(instance.ownsInstance, true);
  assert.deepEqual(app.calls[0], ["lock", { profile: "normal", userDataPath: "C:/Users/test/AppData/my-agent" }]);
  app.listeners.get("second-instance")();
  assert.equal(activations, 0, "activation waits while the startup window is absent");
  window = { id: 1, isDestroyed: () => false };
  instance.windowReady();
  assert.equal(activations, 1);
  app.listeners.get("second-instance")();
  assert.equal(activations, 2);
});

test("a duplicate that cannot acquire the normal profile lock is told to exit", () => {
  const app = fakeApp(false);
  const instance = installWorkbenchSingleInstance({
    app,
    profile: { kind: "normal", userDataPath: "C:/Users/test/AppData/my-agent" },
    getWindow: () => null,
    activateWindow: () => assert.fail("a non-owner must not activate windows"),
  });
  assert.equal(instance.ownsInstance, false);
  assert.equal(app.listeners.has("second-instance"), false);
});

test("fixed-host and isolated evolution profiles do not compete for the normal workbench lock", () => {
  for (const kind of ["fixed-host", "evolution-test"]) {
    const app = fakeApp();
    const instance = installWorkbenchSingleInstance({
      app,
      profile: { kind, userDataPath: `C:/isolated/${kind}` },
      getWindow: () => null,
      activateWindow: () => assert.fail("isolated profile must not install activation"),
    });
    assert.equal(instance.ownsInstance, true);
    assert.equal(app.calls.length, 0);
  }
});

test("restoring a tray-hidden workbench resumes its session without starting a sidecar", async () => {
  const { activateWorkbenchWindow } = await import("../electron/workbench-single-instance.mjs");
  const calls = [];
  const window = {
    isDestroyed: () => false,
    isVisible: () => false,
    isMinimized: () => false,
    restore: () => calls.push("restore"),
    show: () => calls.push("show"),
    focus: () => calls.push("focus"),
  };
  assert.equal(activateWorkbenchWindow(window, (_window, command) => calls.push(command)), true);
  assert.deepEqual(calls, ["resume", "show", "focus"]);
});

test("restoring a visible or minimized workbench does not resume a suspended session", async () => {
  const { activateWorkbenchWindow } = await import("../electron/workbench-single-instance.mjs");
  for (const { visible, minimized } of [{ visible: true, minimized: false }, { visible: true, minimized: true }]) {
    const calls = [];
    const window = {
      isDestroyed: () => false,
      isVisible: () => visible,
      isMinimized: () => minimized,
      restore: () => calls.push("restore"),
      show: () => calls.push("show"),
      focus: () => calls.push("focus"),
    };
    assert.equal(activateWorkbenchWindow(window, (_window, command) => calls.push(command)), true);
    assert.deepEqual(calls, [...(minimized ? ["restore"] : []), "show", "focus"]);
  }
});


test("a second launch waits even if the first window exists but is still loading", () => {
  const app = fakeApp();
  let activations = 0;
  const window = { isDestroyed: () => false };
  const instance = installWorkbenchSingleInstance({
    app, profile: { kind: "normal", userDataPath: "C:/profile" },
    getWindow: () => window, activateWindow: () => { activations += 1; },
  });
  app.listeners.get("second-instance")();
  app.listeners.get("second-instance")();
  assert.equal(activations, 0);
  instance.windowReady();
  assert.equal(activations, 1, "queued requests coalesce and activate once");
});

test("a replacement window is not activated until its renderer is ready", () => {
  const app = fakeApp();
  let activations = 0;
  let window = { isDestroyed: () => false };
  const instance = installWorkbenchSingleInstance({
    app, profile: { kind: "normal", userDataPath: "C:/profile" },
    getWindow: () => window, activateWindow: () => { activations += 1; },
  });
  instance.windowReady();
  instance.windowUnavailable();
  window = { isDestroyed: () => false };
  app.listeners.get("second-instance")();
  assert.equal(activations, 0);
  instance.windowReady();
  assert.equal(activations, 1);
});

test("Electron child processes do not take or compete for the workbench lock", () => {
  for (const processType of ["renderer", "utility", "gpu-process"]) {
    const app = fakeApp();
    const instance = installWorkbenchSingleInstance({
      app, processType, profile: { kind: "normal", userDataPath: "C:/profile" },
      getWindow: () => null, activateWindow: () => assert.fail(),
    });
    assert.equal(instance.ownsInstance, true);
    assert.deepEqual(app.calls, []);
  }
});

test("main acquires and guards the lock before sidecar startup without restarting it on activation", async () => {
  const { readFileSync } = await import("node:fs");
  const main = readFileSync(new URL("../electron/main.ts", import.meta.url), "utf8");
  const setup = main.indexOf("const workbenchInstance = installWorkbenchSingleInstance(");
  const ready = main.indexOf("app.whenReady().then(async () => {");
  const startup = main.indexOf("await runSidecarStartup();", ready);
  assert.ok(setup >= 0 && setup < ready && ready < startup);
  assert.match(main.slice(ready, startup), /if \(!workbenchInstance\.ownsInstance\) return;/);
  assert.match(main.slice(setup, main.indexOf("let quitInProgress", setup)), /if \(!workbenchInstance\.ownsInstance\) app\.quit\(\)/);
  assert.match(main.slice(setup, ready), /activateWorkbenchWindow\(window, sendSessionControl\)/);
  assert.match(main.slice(setup, ready), /workbenchInstance\.windowReady\(\)/);
  assert.match(main.slice(setup, ready), /workbenchInstance\.windowUnavailable\(\)/);
  assert.doesNotMatch(main.slice(setup, main.indexOf("let quitInProgress", setup)), /ensureSidecar|startSidecar|openWorkbenchWindow/);
  assert.doesNotMatch(main, /`r`n/);
});
