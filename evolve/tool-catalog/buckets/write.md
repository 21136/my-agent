# 写文件（write）

> L1 · Phase 23 M4 · Phase 30 收敛。  
> **调用**：`write_text` / `patch_file` 扁平原语（优先）；或 `run_evolved` · `tool_name=<名>`。

| 工具 | 作用 |
|------|------|
| `write_text` | **新建/覆盖**文本文件（路径相对 agent root；见 WRITE-SCOPE） |
| `patch_file` | **改已有**：单片段或结构化多 hunk 原子修补（主路径；统一 diff 解析待后续适配） |
| `copy_move` | 复制或移动文件/目录（agent 树内；`host:` 路径见 builtin + 托管区 overlay） |
| `move_to_trash` | 移到 `_trash/`（可还原语义，非永久删） |
| `design_document` | 按四种软件设计文档类型生成结构化 Markdown 或 DOCX；项目内优先写入 `workspace/<id>/docs/` |

## 已归档

| 工具 | 替代 |
|------|------|
| `append_text` | 新建用 `write_text`；改已有用 `patch_file` |
| `host_copy_move` | `copy_move` + `host:` 路径，或受控 host 写（见 HOST-SCOPE） |
| `host_read` / `host_list` / `host_grep` | builtin `read_file` / `list_dir` / `grep` + `host:` |

## 注意

- 路径一律相对 agent root；项目内优先 `workspace/<id>/…`。
- 大段正文优先 staging + 短路径，避免超长 JSON。
- **换行（BUG-025 · fixed）**：`write_text` / `patch_file` 落盘前规范化 LF，避免 Windows CRLF 上 `\r` 增殖；已污染文件可一次 `write_text` 覆盖清理。
- **Vue / 多行源文件 >6KB**：禁止 inline `content`；用 `workspace/_staging/<name>` + `content_workspace_path`。
- **多行块插入**（empty slot、skeleton）：勿用 `start_line`+`end_line` 单行替多行；用 **find 唯一锚点** 或 staging 整文件。
- **patch_file v2**：可用 `hunks=[{find,replacement}, …]`、`base_hash`、`patch_id`、`dry_run`；所有 hunk 先在内存中校验，任一失败都不落盘。相同 `patch_id` 会查 `data/patch-ledger.jsonl` 并幂等跳过；文件外部变化后不会猜测重放。
- **不要**为「追加一行」再造分域工具；用 `patch_file`。
- **项目绑定**下：`patch_file` 与覆盖已有文件的 `write_text` 由执行器 `write_policy` 分层免确认（仍受 WRITE-SCOPE / 计划门约束）；见 [CONFIRM-PIPELINE.md](../../docs/CONFIRM-PIPELINE.md) §11。
