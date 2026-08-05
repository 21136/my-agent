# 项目（project）

> L1 · Phase 23 M4。调用：`run_evolved` · `tool_name=<名>`（须 `active`）。

| 工具 | 作用 |
|------|------|
| `report_progress` | 勾选/更新项目 `TASKS.md` 进度（须本回合对口证据；见 PROGRESS-GATE） |
| `project_catalog` | 列出 workspace 下已登记项目 |
| `scaffold_project` | 按 `evolve/scaffolds/<recipe>/` 配方初始化项目（见 PROJECT-RECIPES） |
| `run_project_tests` | 跑项目测试并返回结构化 failures（见 PROJECT-VERIFY） |
| `db_migrate_status` | 只读检查 alembic/prisma 迁移状态（见 PROJECT-QUALITY） |
| `run_quality` | 按 ENV.md `quality.commands` 跑 lint（见 PROJECT-QUALITY E11） |

## 注意

- 禁止直接 `write_text` 改 `TASKS.md` 勾选；一律 `report_progress`。
- 项目会话清单含 coding + project scope 工具。
