# 工具索引（本会话默认只看本页）

> Phase 23 · L0。需要细节时：`read_file evolve/tool-catalog/buckets/<桶>.md`  
> **常用扁平原语**（Phase 41）：`run_command` · `write_text` · `patch_file` — 直接调用，无需 `run_evolved` 嵌套  
> 其它 evolved：`run_evolved` · `tool_name=<名>`（须 `status=active`）  
> Builtin（始终）：`read_file` · `list_dir` · `glob_file_search` · `grep` · `web_search` · `fetch_url` · `run_evolved` ·（+ 上三项 proxy）

| 桶 | 何时读 | 路径 |
|----|--------|------|
| 设计文档与图表 | 生成设计文档、图源并渲染为图片 | `buckets/design.md` |
| 写文件 | 新建 `write_text` / 改已有 `patch_file`、搬移、回收站 | `buckets/write.md` |
| 设计文档 | 生成四类设计文档及 Mermaid/PlantUML 图表源 | `buckets/design.md` |
| 执行构建 | `run_command` / `run_service` / `repair_node_modules` / `browser_open` / git_* | `buckets/run.md` |
| 整理 | 按扩展名 / 去重 / 归档 / 重命名 | `buckets/organize.md` |
| 项目 | 进度勾选、项目目录查询 | `buckets/project.md` |
| 进化 | `write_evolve`、克隆进 tools | `buckets/evolve.md` |

权限与路径门禁（confirm、WRITE-SCOPE、项目计划门）仍由执行器强制；本索引不替代它们。  
项目绑定下部分写操作 confirm 由 `write_policy` 分层（见 `docs/CONFIRM-PIPELINE.md` §11）。  
**已归档工具**（不可 `run_evolved`）：见 `docs/ARCHIVED-TOOLS.md` · 各 bucket 的「已归档」表。
