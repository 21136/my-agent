# Archived evolved tools — reference map

> v0.2.0 · 2026-08-05  
> 真源：`evolve/tools/**/tool.toml` 的 `status` 字段；执行面仅 **`active`** 工具可经 `run_evolved` 调用（IT-103 · IT-437）。

## 原则

1. **归档 ≠ 删目录**：保留 `tool.toml` + 目录内 `README.md`；**`main.py` 已删**（T-4310）。
2. **迁移顺序**：运行时直载 → 提示词/目录 → 删 `main.py`（已完成）。
3. **历史证据**：`progress_gate._LEGACY_EVIDENCE_ALIASES` 将旧回合里的 `mvn_exec`/`npm_exec` 等映射到当前工具；**不可再 `run_evolved` 调用** archived 工具。
4. **Guard 逻辑**：E7/E9 → `agent-core/project_npm_guard.py`（经 `run_command`）；pip 校验 → `pip_install_policy.py`。

## 清单（13）

| 工具 | 路径 | 替代 | 备注 |
|------|------|------|------|
| `mvn_exec` | `common/mvn_exec` | `run_command` | T-4307 配方已迁 |
| `npm_exec` | `common/npm_exec` | `run_command` | 同上 |
| `jshell_exec` | `common/jshell_exec` | `run_command` | |
| `pip_install` | `common/pip_install` | `run_command` · `python -m pip install …` | |
| `run_python` | `common/run_python` | `run_command` · `python …` | executor scaffold guard 仍提名字符串 |
| `repl` | `common/repl` | `run_command` | T-4308 archived；≠ CLI `ConversationRepl` |
| `append_text` | `common/append_text` | `patch_file` / `write_text` | Phase 30 Track C |
| `host_read` | `common/host_read` | builtin `read_file` + `host:` | |
| `host_grep` | `common/host_grep` | builtin `grep` + `host:` | |
| `host_list` | `common/host_list` | `glob_file_search` + `host:` | |
| `host_copy_move` | `common/host_copy_move` | `agent-core/host_tools.py` | |
| `study_note` | `workflow/study_note` | `write_text` / 笔记文件 | 已从 `activity_router` 移除 |
| `ws_probe_tool` | `data/ws_probe_tool` | `read_file` + JSON 解析 | |

各工具目录下有 **`README.md`** 短说明（替代路径 + 链到本文）。

## 代码引用（2026-08-05）

### 已迁

| 位置 | 变更 |
|------|------|
| `scaffold_recipes.py` | manifest 仅 `run_command`；旧 kind fail fast |
| `project_verify.py` | 测试套件经 `run_command` |
| `project_npm_guard.py` / `pip_install_policy.py` | 自 archived `main.py` 抽出 |
| `progress_gate.py` | 对口表仅 active；`_LEGACY_EVIDENCE_ALIASES` 兼容旧证据名 |

### 仍允许的名称引用（非执行）

| 位置 | 用途 |
|------|------|
| IT-103 / IT-437 / IT-120 | 复制 archived 目录（`tool.toml`）测拒绝 |
| `test_checker_subagent` | 示例工具名 |
| `scaffold_recipes._DEPRECATED_STEP_KINDS` | 旧 manifest 报错 |

**无** archived `main.py` 直载；registry 对 `status=archived` **不**要求 entry 脚本存在。

## 相关任务

| ID | 内容 | 状态 |
|----|------|------|
| T-4307 | 配方弃用 archived exec | **done** |
| T-4308 | `repl` archived | **done** |
| T-4309 | `project_verify` → `run_command` | **done** |
| T-4310 | 删 archived `main.py` + 目录 README | **done** |
| T-4311 | 文档 + Progress Gate 清理 | **done** |

## 手工验收

1. `run_evolved` → `repl` / `npm_exec` → **不可执行**（IT-103 / IT-437）。
2. 项目模式 archived `repl` 包管理绕过 → E8 硬拒，提示 `run_command`。
3. `run_project_tests` dry_run → shell 字符串，无 `npm_exec` kind。
