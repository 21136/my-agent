# 个人工作流

- 整理 workspace 目录时，优先用 evolved 工具 **`sort_by_extension`**（按扩展名把顶层文件移入 `pdf/`、`txt/`、`_no_ext/` 等子文件夹）。
- 先 `run_evolved` + `dry_run: true` 预览 `moved[]`，用户确认后再正式执行。
- `path` 为相对 **workspace** 的目录路径；仅处理该目录**顶层文件**。
- 需要新建/覆盖文本文件时用 common 的 `write_text`（workspace_only）。
