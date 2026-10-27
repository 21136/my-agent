# Repository Agent Instructions

## Evolution sandbox (stable edits experiment)

When the user asks to evolve the product (new tools, desktop, agent-core) **without touching the stable tree**:

- Write only under **`.worktrees/sandbox/`** after `init-sandbox.bat` (branch `experiment/sandbox`, shared single `.git`).
- Do **not** modify stable copies at repo root `agent-core/` or `desktop/` for experimental work unless the user explicitly merges or asks to change stable.
- Preview UI: port **5174** serves sandbox `desktop/` when the worktree exists; stable runs on **5173**.
- Promote to stable: **git merge** (or PR), not URL swap alone. After merge, **sync sandbox worktree** (`sync-sandbox-worktree.bat` or 壳预览 → 同步 sandbox 与稳定). See [docs/SANDBOX-EVOLUTION.md](docs/SANDBOX-EVOLUTION.md).
- **「采用预览为主壳」** persists `data/state.json` → `desktop_shell_renderer_url`; use 壳预览 → **主壳恢复稳定 5173** if the main window should stay on stable lane.

## Python runtime

- On Windows, prefer `.venv\Scripts\python.exe` whenever that file exists.
- Before running Python-dependent validation, verify `sys.executable` and import the required packages with that interpreter.
- Do not rely on packages installed only in the user site directory; the Codex execution sandbox may not be able to read that directory.
- If `.venv` does not exist, use the system `python` and report any missing dependency instead of assuming a user-site installation is visible.
