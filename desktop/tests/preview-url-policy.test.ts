import assert from "node:assert/strict";
import { test } from "node:test";
import {
  defaultShellDevPreviewBase,
  defaultShellMainDevBase,
  deriveCandidatePreviewUrl,
  isAllowedShellDevPreviewNavigation,
  loopbackUrlVariants,
  normalizeShellDevPreviewBase,
} from "../electron/preview-url-policy.ts";

test("shell dev preview only allows loopback origins matching the configured base", () => {
  const base = normalizeShellDevPreviewBase("http://127.0.0.1:5173/")!;
  assert.equal(base, "http://127.0.0.1:5173/");
  assert.equal(isAllowedShellDevPreviewNavigation("http://127.0.0.1:5173/", base), true);
  assert.equal(isAllowedShellDevPreviewNavigation("http://127.0.0.1:5173/index.html", base), true);
  assert.equal(isAllowedShellDevPreviewNavigation("https://evil.test/", base), false);
  assert.equal(isAllowedShellDevPreviewNavigation("javascript:alert(1)", base), false);
  assert.equal(isAllowedShellDevPreviewNavigation("http://127.0.0.1:3000/", base), false);
});

test("preview lane defaults to main dev port + 1 for dual Vite servers", () => {
  const prevMain = process.env.VITE_DEV_SERVER_URL;
  const prevPreview = process.env.MY_AGENT_SHELL_PREVIEW_URL;
  delete process.env.MY_AGENT_SHELL_PREVIEW_URL;
  process.env.VITE_DEV_SERVER_URL = "http://127.0.0.1:5173";
  assert.equal(defaultShellMainDevBase(), "http://127.0.0.1:5173/");
  assert.equal(deriveCandidatePreviewUrl(defaultShellMainDevBase()), "http://127.0.0.1:5174/");
  assert.equal(defaultShellDevPreviewBase(), "http://127.0.0.1:5174/");
  if (prevMain === undefined) delete process.env.VITE_DEV_SERVER_URL;
  else process.env.VITE_DEV_SERVER_URL = prevMain;
  if (prevPreview === undefined) delete process.env.MY_AGENT_SHELL_PREVIEW_URL;
  else process.env.MY_AGENT_SHELL_PREVIEW_URL = prevPreview;
});

test("loopbackUrlVariants includes localhost and 127.0.0.1", () => {
  const variants = loopbackUrlVariants("http://127.0.0.1:5174/");
  assert.deepEqual(variants.sort(), ["http://127.0.0.1:5174/", "http://localhost:5174/"].sort());
});

test("MY_AGENT_SHELL_PREVIEW_URL overrides candidate port", () => {
  const prev = process.env.MY_AGENT_SHELL_PREVIEW_URL;
  process.env.MY_AGENT_SHELL_PREVIEW_URL = "http://127.0.0.1:5199/";
  assert.equal(defaultShellDevPreviewBase(), "http://127.0.0.1:5199/");
  if (prev === undefined) delete process.env.MY_AGENT_SHELL_PREVIEW_URL;
  else process.env.MY_AGENT_SHELL_PREVIEW_URL = prev;
});
