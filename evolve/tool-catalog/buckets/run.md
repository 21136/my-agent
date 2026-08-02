# 执行构建（run）

> L1 · Phase 23 M4 · Phase 28 更新。调用：`run_evolved` · `tool_name=<名>`（须 `active`）。

| 工具 | 作用 |
|------|------|
| `run_command` | **通用 shell（主路径）**：一次性捕获 stdout/stderr/exit；`background:true` 升格为 `run_service` 托管 |
| `run_service` | **托管长驻进程（主路径）**：start/stop/status/logs/wait；也可由 `run_command` background 升格登记 |
| `repair_node_modules` | **显式**清依赖重装：删 `node_modules`（可选 lock）→ npm/pnpm/yarn install；须点名，非剧本 |
| `dev_start` | 一键前后端的**薄封装**（内部只调 `run_service`）；单服务请直接用 `run_service` |
| `http_request` | HTTP 探活 / 调 API（loopback GET/HEAD 不 confirm；其它须 confirm）。**探本地勿用** builtin `fetch_url` |
| `browser_open` | 系统默认浏览器打开 http(s)（loopback 免确认；外网须确认） |
| `db_query` | SQLite 速查（默认只读 SELECT；`write=true` 须 confirm） |
| `run_demo` | 在 `agent-core/` 下跑 `python <script>.py` 冒烟 |
| `run_tests` | 批量跑约定 demo / 测试套件 |
| `git_snapshot` | 只读：`git status --porcelain` + `diff --stat`（可选 staged） |
| `git_commit` | 受控提交：`add` + `commit`（禁 push/force/amend）；`dry_run` 可预览 |
| `git_branch` | 受控分支：`list` / `create` / `switch`（禁 force checkout） |
| `git_push` | 受控推送：仅当前分支 → remote（禁 force；永远确认） |
| `csv_head` | 预览 CSV 前 N 行（表头、列类型、总行数） |
| `doc_parser` | 解析 `.doc` / `.docx` / `.xlsx` 为可读文本（支持 `host:`） |

## 已归档（勿调）

| 工具 | 替代 |
|------|------|
| `mvn_exec` / `npm_exec` / `jshell_exec` | **`run_command`** |
| `run_python` / `pip_install` | **`run_command`**（`python …` / `python -m pip install …`） |
| `repl` | status=`suspect`（出执行面）；一次性表达式用 `run_command` |

## 注意

- **一次性命令**（build/test/install/脚本）：主用 **`run_command`**（[SHELL-CHANNEL.md](../../../docs/SHELL-CHANNEL.md)）。
- **长驻 / 后台**：优先 `run_service`；或 `run_command` + `background:true`（Phase 31 D1 升格，返回 `name` + 日志尾）。
- 测前端：目标目录已有 `node_modules` 时**不要先 install**；直接 `npm run build` / `test`（除非需重装）。
- **依赖损坏 / 半截 `node_modules`**：点名 `repair_node_modules`（working_dir=前端目录）；勿反复 `npm run dev`。`run_command` 对 install/rmdir 已有长超时，但专用工具删目录更稳（shutil + 权限重试）。
- 路径一律相对 agent root；项目内优先 `workspace/<id>/…`。
- **长驻服务**（监听端口、不退出）：用 **`run_service`** 或 `run_command` background；不要用无 background 的 `run_command` 起不退出进程。详见 [RUN-SERVICE.md](../../../docs/RUN-SERVICE.md)。
- **执行可见性**：同意工具后桌面会显示运行卡与耗时；项目侧栏 **Services** 可刷新登记服务/日志尾（[EXEC-OBSERVABILITY.md](../../../docs/EXEC-OBSERVABILITY.md)）。
- **探活 / 调本地 API**：`http_request`（勿用 `fetch_url` 打 localhost）。
- **打开页面给人看**：`browser_open`（系统浏览器；无头截图未做）。
- **端口占用**：`run_service` · `port_status` / `kill_port`（杀端口须 confirm）。
- **提交 / 分支 / 推送**：`git_commit`（add+commit）；`git_branch`（list/create/switch）；`git_push`（当前分支，禁 force，须确认）。
- **SQLite**：`db_query`；远程库不在本工具范围。
- **装 Python 包**：`run_command` → `python -m pip install …`（须确认）。
