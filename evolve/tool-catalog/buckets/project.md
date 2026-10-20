# 项目（project）

> L1 · Phase 23 M4。active 工具可通过 `run_evolved` 调用；experimental 工具仅记录能力，未进入默认工具清单。

| 工具 | 作用 |
|------|------|
| `deliverable_review` | 交付审查子代理（只读）；口语验收/还缺什么/能交付吗 |
| `project_catalog` | 列出 workspace 项目；用户明确要求接续时，可用 `include_threads=true, project_id=<当前项目>` 查看本项目活/归档线摘要 |
| `scaffold_project` | 按 `evolve/scaffolds/<recipe>/` 配方初始化项目（见 PROJECT-RECIPES） |
| `structured_test` | **active**：统一测试 status/exit/duration/failures/rerun；底层复用 `run_project_tests` |
| `run_project_tests` | 跑项目测试并返回结构化 failures（见 PROJECT-VERIFY） |
| `db_migrate_status` | 只读检查 alembic/prisma 迁移状态（见 PROJECT-QUALITY） |
| `run_quality` | 按 ENV.md `quality.commands` 跑 lint（见 PROJECT-QUALITY E11） |

## 项目聊天接续

- 默认 `project_catalog` 仍只返回项目目录；`include_threads=true` 时必须同时传入 `project_id`，结果只包含该项目的活线与最近归档线摘要。
- 只在用户明确要求接续时查询；只从 `state=archived` 记录中选择旧线，当前活线不是旧线候选。按用户提到的任务/时间选择；候选有歧义先问，不把完整旧聊天灌入当前上下文。
- 需要读取内容时使用结果里的 `messages_path` 调用 `read_file`；跨会话读取保留现有 confirm。旧会话中的完成声明不替代当前代码核验。

## 注意

- 不再维护 `TASKS.md` 任务清单；完成与否以验证命令的退出码为准（见 `skills/plan-and-verify`）。
- 项目会话清单含 coding + project scope 工具。
