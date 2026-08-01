# 写文件（write）

> L1 · Phase 23 M4。调用：`run_evolved` · `tool_name=<名>`（须 `active`）。

| 工具 | 作用 |
|------|------|
| `write_text` | 新建/覆盖文本文件（路径相对 agent root；见 WRITE-SCOPE） |
| `append_text` | 追加文本到已有文件末尾 |
| `copy_move` | 复制或移动文件/目录（agent 树内） |
| `move_to_trash` | 移到 `_trash/`（可还原语义，非永久删） |
| `patch_file` | 按 unified diff / 片段修补已有文件 |
| `host_copy_move` | 在已登记 `host:` 与 workspace 间复制（受 host_scope） |

## 注意

- 路径一律相对 agent root；项目内优先 `workspace/<id>/…`。
- 大段正文优先 staging + 短路径，避免超长 JSON。
