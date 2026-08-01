# 个人工作流

## 整理类 evolved 工具

以下工具经 `run_evolved` 调用（`status=active` 即可，**不**要求先确认 workflow 主题）。**先 `dry_run: true` 预览**，用户确认后再正式执行。`path` 均相对 **workspace**。  
细节也可：`read_file evolve/tool-catalog/buckets/organize.md`。

| 工具 | 作用 |
|------|------|
| `sort_by_extension` | 顶层文件按扩展名移入 `pdf/`、`txt/`、`_no_ext/` 等子文件夹 |
| `rename_batch` | 顶层文件批量重命名：`prefix` / `suffix` / `replace` / `number` |
| `flatten_dir` | 把子目录中的文件提升到指定目录顶层 |
| `dedupe_by_name` | 按文件名扫描重复项（**只报告**，不删除） |
| `archive_by_date` | 顶层文件按修改日期移入 `YYYY-MM/` 子文件夹 |

典型顺序：`dedupe_by_name` 查重 → `sort_by_extension` 或 `archive_by_date` 分类 → `rename_batch` 统一命名 → `flatten_dir` 收拢深层文件。

## common 文件工具

`write_text` 写整文件 · `append_text` 追加 · `copy_move` 复制/移动 · `move_to_trash` 软删除到 `_trash/`。
