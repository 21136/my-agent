# `npm_exec` (archived)

`status = archived` · `main.py` removed (T-4310).

Use **`run_command`** (`command: "npm …"`, `working_dir`).

E7/E9 logic lives in `agent-core/project_npm_guard.py` (wired via `run_command`).

Reference: [`docs/ARCHIVED-TOOLS.md`](../../../../docs/ARCHIVED-TOOLS.md).
