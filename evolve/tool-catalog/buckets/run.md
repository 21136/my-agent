# 执行构建（run）

> L1 · Phase 23 M4。调用：`run_evolved` · `tool_name=<名>`（须 `active`）。

| 工具 | 作用 |
|------|------|
| `run_python` | 跑 agent root 下 Python 脚本，返回 stdout/stderr/exit_code |
| `repl` | 会话内交互 Python（`session_id` 保状态）。**项目模式禁止**用 repl 跑 npm/mvn |
| `npm_exec` | 在指定目录跑 npm/pnpm/yarn；读项目 `ENV.md`；目录参数用 `working_dir`（可用别名 `cwd`） |
| `mvn_exec` | 跑 Maven；同样读 `ENV.md`；用 `working_dir` |
| `run_service` | **托管长驻进程**（主路径）：start/stop/status/logs/wait |
| `dev_start` | 一键前后端的**薄封装**（内部只调 `run_service`）；单服务请直接用 `run_service` |
| `http_request` | HTTP 探活 / 调 API（loopback GET/HEAD 不 confirm；其它须 confirm）。**探本地勿用** builtin `fetch_url` |
| `db_query` | SQLite 速查（默认只读 SELECT；`write=true` 须 confirm） |
| `pip_install` | `python -m pip install`（packages 或 requirements；dry_run 可预览） |
| `run_demo` | 在 `agent-core/` 下跑 `python <script>.py` 冒烟 |
| `run_tests` | 批量跑约定 demo / 测试套件 |
| `git_snapshot` | 只读：`git status --porcelain` + `diff --stat`（可选 staged） |
| `git_commit` | 受控提交：`add` + `commit`（禁 push/force/amend）；`dry_run` 可预览 |
| `csv_head` | 预览 CSV 前 N 行（表头、列类型、总行数） |
| `doc_parser` | 解析 `.doc` / `.docx` / `.xlsx` 为可读文本（支持 `host:`） |

## 注意

- 测前端：目标目录已有 `node_modules` 时**不要先 install**；直接 `run` / `build` / `test`（除非 `force_install`）。
- 路径一律相对 agent root；项目内优先 `workspace/<id>/…`。
- **长驻服务**（监听端口、不退出）：用 **`run_service`**（主路径）；`dev_start` 仅作前后端一键糖。不要 `mvn_exec` / `npm_exec` / `repl`。详见 [RUN-SERVICE.md](../../../docs/RUN-SERVICE.md) · [PROJECT-DEV-TOOLS.md](../../../docs/PROJECT-DEV-TOOLS.md)。
- **探活 / 调本地 API**：`http_request`（勿用 `fetch_url` 打 localhost）。
- **端口占用**：`run_service` · `port_status` / `kill_port`（杀端口须 confirm）。
- **提交代码**：`git_commit`（仅 add+commit）；推送仍由人来。
- **SQLite**：`db_query`；远程库不在本工具范围。
- **装 Python 包**：`pip_install`（勿把可疑参数塞进 packages）。
