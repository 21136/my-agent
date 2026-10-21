import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { readAgentImageDataUrl } from "../electron/agent-file-preview.ts";

const root = mkdtempSync(join(tmpdir(), "ma-img-preview-"));
const pngPath = join(root, "workspace", "_drops", "test.png");
mkdirSync(join(root, "workspace", "_drops"), { recursive: true });
writeFileSync(pngPath, Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00]));

assert.equal(readAgentImageDataUrl(root, "workspace/_drops/test.png")?.startsWith("data:image/png;base64,"), true);
assert.equal(readAgentImageDataUrl(root, "../outside.png"), null);
assert.equal(readAgentImageDataUrl(root, "workspace/_drops/test.txt"), null);

console.log("agent-file-preview.test.ts ok");
