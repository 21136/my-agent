# 工具索引（本会话默认只看本页）

> 直接工具：`read_file` · `write_text` · `patch_file` · `run_command` · `git_diff` · `git_snapshot`；不读 bucket。
> 改文件用 `patch_file`；`write_text` 用于新建/整文件重写；`read_file` 可分段读取。

> 其它 evolved 才在需要参数细节时 `read_file evolve/tool-catalog/buckets/<桶>.md`；`run_evolved` · `tool_name=<名>`（须 `status=active`）  
> Builtins (agent mode; session-independent): `read_file` · `list_dir` · `grep` · `glob_file_search` · `codebase_search` · `web_search` · `fetch_url` · `show_images` · `generate_image` · `run_evolved` · `propose_context_switch` · `deliverable_review` · `explore` · `evolution`
> agent：普通会话与项目会话工具集一致；ask 隐藏执行类与生图；演化入口仍可见但 ask/read-only 只允许 inspect；可见不等于授权。
> `show_images`: 只读，在 Desktop 聊天内联展示已有图片路径（无 confirm、无 API 费用）；生图用 `generate_image`。
> `generate_image`: 仅响应明确生图请求；调用前确认，参考图获准后才上传（可能消耗额度）；本轮图用 `current_attachment`，历史图按上下文查找，歧义时请用户选择。

| 桶 | 何时读 | 路径 |
|----|--------|------|
| 写文件 | `copy_move` / `move_to_trash`、大文件 staging | `buckets/write.md` |
| 设计文档 | 生成四类设计文档及 Mermaid/PlantUML 图表源 | `buckets/design.md` |
| 执行构建 | `run_service` / `repair_node_modules` / `browser_open` / git_* | `buckets/run.md` |
| 整理 | 按扩展名 / 去重 / 归档 / 重命名 | `buckets/organize.md` |
| 项目 | 进度勾选、项目目录查询 | `buckets/project.md` |
| 进化 | `write_evolve`、克隆进 tools | `buckets/evolve.md` |

权限与路径门禁（confirm、WRITE-SCOPE、项目计划门）仍由执行器强制；本索引不替代它们。  
项目绑定下部分写操作 confirm 由 `write_policy` 分层（见 `docs/CONFIRM-PIPELINE.md` §11）。  
**已归档工具**（不可 `run_evolved`）：见 `docs/ARCHIVED-TOOLS.md` · 各 bucket 的「已归档」表。
