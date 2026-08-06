# 项目（project）

> L1 · Phase 23 M4。调用：`run_evolved` · `tool_name=<名>`（须 `active`）。

| 工具 | 作用 |
|------|------|
| `deliverable_review` | 交付审查子代理（只读）；口语验收/还缺什么/能交付吗 |
| `report_progress` | ritual：勾选派进度；solo：可选 |
| `project_catalog` | 列出 workspace 下已登记项目 |
| `scaffold_project` | 按 `evolve/scaffolds/<recipe>/` 配方初始化项目（见 PROJECT-RECIPES） |
| `run_project_tests` | 跑项目测试并返回结构化 failures（见 PROJECT-VERIFY） |
| `db_migrate_status` | 只读检查 alembic/prisma 迁移状态（见 PROJECT-QUALITY） |
| `run_quality` | 按 ENV.md `quality.commands` 跑 lint（见 PROJECT-QUALITY E11） |

## 注意

- 禁止直接 `write_text` 改 `TASKS.md` 勾选；ritual 一律 `report_progress`；solo 可用侧栏勾选。
- 项目会话清单含 coding + project scope 工具。
