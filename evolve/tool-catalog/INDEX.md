# 工具索引（本会话默认只看本页）

> 直接工具：`read_file` · `write_text` · `patch_file` · `run_command` · `git_diff` · `git_snapshot`；不要**为它们读 bucket**。改已有文件用 `patch_file`；`write_text` 仅新建或几乎整文件重写；`read_file` 可分段读。
> 其它 evolved 需要参数细节时读 `evolve/tool-catalog/buckets/<桶>.md`；用 `run_evolved` + `tool_name=<名>`，须 `status=active`。
> Builtins (agent)：`read_file` · `list_dir` · `grep` · `glob_file_search` · `codebase_search` · `web_search` · `fetch_url` · `show_images` · `generate_image` · `generate_video` · `run_evolved` · `propose_context_switch` · `deliverable_review` · `explore` · `evolution`。普通会话与项目会话工具集一致；ask 隐藏执行、生图、视频；演化入口仅 inspect；可见不等于授权。
> `show_images` 只读；Desktop 内联展示本机媒体/文本/代码，无 confirm/API 费用；`query` 可浅搜桌面、下载、文档。
> `generate_image` 仅明确请求；先确认，获准参考图才上传（可能耗额度）。本轮图用 `current_attachment`，历史图按上下文找；歧义请用户选择。
> `generate_video` 默认标准版，文生或本地 PNG 图生视频；明确请求、确认参考图来源并获准后上传（付费）。选图规则同上；用 `request_id` 续查，勿重复提交。

按需查桶：写文件 `buckets/write.md`；设计 `buckets/design.md`；执行 `buckets/run.md`；整理 `buckets/organize.md`；项目 `buckets/project.md`；进化 `buckets/evolve.md`。

权限与路径门禁（confirm、WRITE-SCOPE、项目计划门）由执行器强制；项目绑定下部分写操作由 `write_policy` 分层确认（`docs/CONFIRM-PIPELINE.md` §11）。
**已归档工具**（不可 `run_evolved`）：`docs/ARCHIVED-TOOLS.md` 及各 bucket 的「已归档」表。
