# 整理（organize）

> L1 · Phase 23 M4。调用：`run_evolved` · `tool_name=<名>`（须 `active`）。

| 工具 | 作用 |
|------|------|
| `sort_by_extension` | 按扩展名分到子目录 |
| `rename_batch` | 批量重命名（模式/映射） |
| `dedupe_by_name` | 同名去重（保留策略可配） |
| `flatten_dir` | 打平嵌套目录 |
| `extract_archive` | 解压 zip/7z 等（默认 Bandizip；fallback PowerShell/Python） |
| `archive_by_date` | 按日期归档到子目录 |

`study_note` 已 **archived** → 用 `write_text` 写笔记文件。

## 注意

- 写前先 `list_dir` / `dry_run`（若支持）；大目录操作须 confirm。
- 路径相对 agent root；勿越过 deny-list。
