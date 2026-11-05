import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { readAgentImageDataUrl, readAgentTextPreview } from "../electron/agent-file-preview.ts";

const root = mkdtempSync(join(tmpdir(), "ma-img-preview-"));
const pngPath = join(root, "workspace", "_drops", "test.png");
mkdirSync(join(root, "workspace", "_drops"), { recursive: true });
writeFileSync(pngPath, Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00]));

assert.equal(readAgentImageDataUrl(root, "workspace/_drops/test.png")?.startsWith("data:image/png;base64,"), true);
assert.equal(readAgentImageDataUrl(root, "../outside.png"), null);
assert.equal(readAgentImageDataUrl(root, "workspace/_drops/test.txt"), null);

const outsideDir = mkdtempSync(join(tmpdir(), "ma-img-outside-"));
const outsidePng = join(outsideDir, "desk.png");
writeFileSync(outsidePng, Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00]));
const localRef = `local:${outsidePng.replace(/\\/g, "/")}`;
assert.equal(readAgentImageDataUrl(root, localRef)?.startsWith("data:image/png;base64,"), true);

const sshKey = join(outsideDir, ".ssh", "id_rsa");
mkdirSync(join(outsideDir, ".ssh"), { recursive: true });
writeFileSync(sshKey, "secret");
assert.equal(readAgentImageDataUrl(root, `local:${sshKey.replace(/\\/g, "/")}`), null);

const tsPath = join(root, "workspace", "app.ts");
writeFileSync(tsPath, "export const n = 1;\n");
const preview = readAgentTextPreview(root, "workspace/app.ts");
assert.equal(preview?.language, "typescript");
assert.match(preview?.text ?? "", /export const n/);
assert.equal(readAgentTextPreview(root, `local:${sshKey.replace(/\\/g, "/")}`), null);

console.log("agent-file-preview.test.ts ok");
