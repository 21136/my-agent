# 进化（evolve）

> L1 · Phase 23（M4 桶 + **Mr** 手册）。调用：`run_evolved` · `tool_name=<名>`（须 `active`）。  
> 造工具细则以本文件为准；`core.txt` 只留硬边界 + 指针。

| 工具 | 作用 |
|------|------|
| `write_evolve` | 向 `evolve/tools/<scope>/<name>/` 写 `main.py` / `tool.toml`（及 README）。**每次 confirm**，无会话 `a` |
| `git_clone` | 浅克隆公开 **https** 仓；项目线 workspace 目标限当前 `project_root`，evolve_tools 目标需显式请求；每次 confirm。 |

## 项目窗口
- 项目会话也可在用户明确要求时调用 `write_evolve` 或 `git_clone(target=evolve_tools)`；只能沉淀到 `evolve/tools/`，不得借此写其他项目文件。
- `write_evolve` 项目会话每次单独确认，不能由普通确认预算覆盖；`git_clone` 每次确认。`suspect` / `archived` 工具不可执行。

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

- 新工具 `status→active` 后：必须更新 `evolve/tool-catalog/INDEX.md` 的主索引行，并新增/更新对应 `buckets/<桶>.md`，写清参数、输出、示例和适用范围；至少验证一次 `INDEX → bucket → run_evolved` 链路。
- 可用 `run_demo` / checker 验收后再标 active。
