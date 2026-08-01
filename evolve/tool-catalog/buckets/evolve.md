# 进化（evolve）

> L1 · Phase 23（M4 桶 + **Mr** 手册）。调用：`run_evolved` · `tool_name=<名>`（须 `active`）。  
> 造工具细则以本文件为准；`core.txt` 只留硬边界 + 指针。

| 工具 | 作用 |
|------|------|
| `write_evolve` | 向 `evolve/tools/<scope>/<name>/` 写 `main.py` / `tool.toml`（及 README）。**每次 confirm**，无会话 `a` |
| `git_clone` | 浅克隆公开 **https** 仓到 `workspace/` 或 `evolve/tools/`（`target`）；每次 confirm。项目线通常仅允许 workspace |

## 项目窗口

- **项目窗口**禁止 `write_evolve` / 向 `evolve/tools` clone；要沉淀能力请到**普通窗口**。
- `suspect` / `archived` 工具不在执行面，也不应写入 INDEX。

## `write_evolve` 逐步手册

通过 `run_evolved` 脚手架**新** evolved 工具时：

1. 把 **`path` 与内容字段放在 `run_evolved` 顶层**（与 `tool_name` 同级），`arguments: {}`。**禁止**把 TOML 塞进 `arguments.content`。
2. **一文件一调用** — 先 `main.py`，再 `tool.toml`（两边齐备前可用 `status: draft`）。
3. **`tool.toml`**：`content_base64`（UTF-8 标准 base64）**或** `content_workspace_path`。
4. **大段 `main.py` / README**（>~200 字符、多行或含引号）：优先 **`write_text` → `workspace/_staging_*.py`**，再 `write_evolve` 用 **`content_workspace_path`**。不要依赖超长 `content_base64`（易截断 / padding 错）。
5. 短片段仍可用 `content_base64`；非法 base64 在 confirm **之前**会被拒。
6. 脚手架用 **`on_conflict`: `overwrite`**（默认 `skip` 遇已存在会失败）。
7. 不要把最终文件名 `main.py` / `tool.toml` 用 `write_text` 写进 workspace 当成品 — 只写 `_staging*`，再走 `content_workspace_path`。
8. 每次写入须用户 confirm；`write_evolve` **无**会话 `a` 免确认捷径。

## 落盘后

- 新工具 `status→active` 后：更新本目录对应桶 + 必要时改 `INDEX.md` 一行。
- 可用 `run_demo` / checker 验收后再标 active。
