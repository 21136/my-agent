/**
 * Shell preview lane (5174): renderer HMR only — no Electron, no sidecar.
 * Embedded by the main workbench via WebContentsView.
 */
import os from "node:os";
import path from "node:path";
import { defineConfig } from "vite";

const repoRoot = path.resolve(__dirname, "..");
const localCacheRoot = path.join(
  process.env.LOCALAPPDATA || process.env.TEMP || os.tmpdir(),
  "my-agent-desktop",
);

const devWatchIgnored = [
  "**/node_modules/**",
  "**/dist-electron/**",
  path.join(__dirname, "dist-electron/**"),
  path.join(__dirname, "mockups/**"),
  path.join(repoRoot, "agent-core/**"),
  path.join(repoRoot, "data/**"),
  path.join(repoRoot, "evolve/**"),
  path.join(repoRoot, "workspace/**"),
  path.join(repoRoot, "_trash/**"),
  path.join(repoRoot, "docs/**"),
];

export default defineConfig({
  cacheDir: path.join(localCacheRoot, "vite-cache-shell-preview"),
  plugins: [],
  build: {
    rollupOptions: {
      input: {
        main: path.resolve(__dirname, "index.html"),
      },
      output: {
        manualChunks(id) {
          if (
            id.includes("node_modules/mermaid")
            || id.includes("node_modules/cytoscape")
            || id.includes("node_modules/katex")
            || id.includes("node_modules/dagre")
            || id.includes("node_modules/d3")
          ) {
            return "vendor-mermaid";
          }
          if (id.includes("node_modules/highlight.js") || id.includes("node_modules/marked")) {
            return "vendor-markdown";
          }
          return undefined;
        },
      },
    },
  },
  server: {
    host: "127.0.0.1",
    port: 5174,
    strictPort: true,
    watch: {
      ignored: devWatchIgnored,
    },
  },
});
