# 工具索引（本会话默认只看本页）

> Phase 23 · L0。需要细节时：`read_file evolve/tool-catalog/buckets/<桶>.md`  
> 调用：`run_evolved` · `tool_name=<名>`（须 `status=active`）  
> Builtin（始终）：`read_file` · `list_dir` · `grep` · `web_search` · `fetch_url` · `run_evolved`

| 桶 | 何时读 | 路径 |
|----|--------|------|
| 写文件 | 新建/改文本、搬移、回收站、补丁 | `buckets/write.md` |
| 执行构建 | npm/mvn/python/测试/demo、CSV/文档预览 | `buckets/run.md` |
| 整理 | 按扩展名 / 去重 / 归档 / 重命名 | `buckets/organize.md` |
| 项目 | 进度勾选、项目目录查询 | `buckets/project.md` |
| 进化 | `write_evolve`、克隆进 tools | `buckets/evolve.md` |

权限与路径门禁（confirm、WRITE-SCOPE、项目计划门）仍由执行器强制；本索引不替代它们。
