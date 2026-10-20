# 项目历史会话按需接续 Implementation Plan

> **For agentic workers:** Implement inline task-by-task; follow the test-driven-development skill. Steps use checkbox syntax for tracking.

**Goal:** 在用户明确要求接续时，让 Agent 能定位并只读当前项目的归档会话，再按用户要求整理交接；新开线本身仍是空白会话。

**Architecture:** 扩展现有 `project_catalog` 的可选历史线查询模式，只返回指定项目的受限会话元数据，不读取完整聊天内容。Agent 根据本轮用户请求选择候选；完整旧会话仍通过现有 `read_file` / `grep` 路径读取，并继续触发跨会话确认。提示词只在项目绑定会话中说明这一按需流程。

**Tech Stack:** Python 3.11+、现有 evolved tool TOML manifest、pytest、TypeScript 不涉及。

**Spec:** `docs/PROJECT-THREADS.md` §0 T4/T5、§3.2、§7.2。

## Global Constraints

- 新开项目线不自动读取旧线、不注入固定摘要、不修改项目任务文件。
- 历史线查询必须限定到一个已登记项目；Agent Core 注入的当前 `project_id` 是权威绑定，并覆盖模型参数；归档会话元数据必须再次核对其 `project_id`。
- 旧线候选只从 `state=archived` 记录中选择；当前活线不得被误当成来源会话。
- 完整聊天内容只在用户明确要求后经现有跨会话读取确认流程访问。
- 不把旧会话中的完成声明视为当前代码事实；执行前复核当前项目状态。
- 保留现有工作区的无关未提交改动；不提交、不批量暂存。

---

### Task 1: 为 project_catalog 增加单项目历史线摘要

**Files:**
- Modify: `agent-core/session.py` — 给单会话摘要增加首条/最近真实用户消息字段。
- Modify: `agent-core/tools/executor.py`、`agent-core/tools/builtin/run_evolved.py` — 将当前项目绑定作为不可由模型伪造的内部上下文传入 `project_catalog`。
- Modify: `evolve/tools/common/project_catalog/main.py` — 支持 `include_threads=true` 与必填 `project_id`，校验并限制到该项目，输出活线及最近归档线的时间、消息数、首条/最近用户消息；拒绝未绑定或其他项目请求，忽略内部/无效/绑定不匹配的会话，并报告截断数。
- Modify: `evolve/tools/common/project_catalog/tool.toml` — 声明新输入参数。
- Create: `agent-core/tests/test_project_catalog_history.py` — 隔离测试项目、活线、归档线及跨项目排除。

**Interfaces:** `run_project_catalog(payload, *, paths=None) -> dict[str, Any]`；默认调用路径保持原有项目目录输出，只有 `include_threads is True` 时才进入单项目线程模式。

- [x] **Step 1: 先写失败测试**：用临时 AgentPaths 创建两个项目，各建活线/归档线及可识别的 user 消息；断言查询只返回所选项目并包含活/归档状态和首/末消息；另测缺少 project_id、未知项目、错误项目绑定和默认模式。
- [x] **Step 2: 运行测试确认 RED**：`F:\my-agent-main\.venv\Scripts\python.exe -m pytest agent-core/tests/test_project_catalog_history.py -q`；缺少 `paths` 注入或 `include_threads` 实现时应按预期失败。
- [x] **Step 3: 最小实现**：复用现有 `project_switch` 归档索引和 `session_summary_for_id`；校验会话 ID 为单一路径段、过滤内部会话并确认摘要中的 project_id 与所选项目一致；限制结果为活线加最多 20 条近期归档线。
- [x] **Step 4: 运行同一测试确认 GREEN**：上述 pytest 命令全部通过。

### Task 2: 仅在用户请求接续时教会 Agent 使用历史线

**Files:**
- Modify: `agent-core/loader.py` — 项目绑定会话提示按需调用历史目录、遇到歧义先询问、选中后读取旧会话并保持现有 confirm。
- Modify: `evolve/tool-catalog/buckets/project.md` — 文档说明历史线查询参数和边界。
- Modify: `docs/PROJECT-THREADS.md` — 补充本次会话发现/读取流程与修订记录。
- Create: `agent-core/tests/test_project_handoff_prompt.py` — 覆盖项目会话提示与普通会话不注入专用交接指引。

- [x] **Step 1: 先写失败测试**：断言绑定项目的 capability hints 包含“仅用户明确要求”“当前 project_id”“歧义先问”“旧线读取须 confirm”“复核当前代码”；普通会话不含项目历史接续指引。
- [x] **Step 2: 运行测试确认 RED**：`F:\my-agent-main\.venv\Scripts\python.exe -m pytest agent-core/tests/test_project_handoff_prompt.py -q`。
- [x] **Step 3: 最小实现与文档更新**：只扩展提示和工具文档，不新增 UI、不改变新开线逻辑。
- [x] **Step 4: 运行聚焦回归**：`F:\my-agent-main\.venv\Scripts\python.exe -m pytest agent-core/tests/test_project_catalog_history.py agent-core/tests/test_project_handoff_prompt.py agent-core/tests/test_project_threads.py agent-core/tests/test_tool_catalog_m4.py -q`；再以 `-k CrossSessionReadConfirmTests` 验证既有确认门，并运行 `git diff --check`。
