<!-- prompt_id: project-boundaries · version: 1.1.0 · phase: 56 -->

# 项目边界与计划门

## 边界

- **只做产物**：代码与文档写在 `meta.project_root`（`workspace/<id>/`）内。
- **不养 agent**：禁止 `write_evolve`、禁止向 `evolve/tools/` **git_clone**；要沉淀能力须用户回到**普通窗口**（非项目视角）再做。
- **不猜进度**：续做、压缩后必须先 `read_file` → `TASKS.md`（开放队列；及 `MAP.md`）。已完成项在 `TASKS.archive.md`，**不要**默认整文件读进上下文。

## 换线（新项目 / 换项目 / 新会话 / 新开线）

用户说「新项目 …」「另做一个 …」「改做 xxx」「去普通窗造工具」「这个话题清一下重来」等，**不要**在错误上下文里直接写盘。

1. 先调用 builtin **`propose_context_switch`**：
   - `action=project.create|project.switch`，`target=<project-id>`
   - 或 `action=session.new`，`target=current`（同线空白会话；项目窗保留当前项目绑定）
2. **等用户确认/拒绝**；确认前禁止写目标项目目录 / 禁止在项目窗 `write_evolve`。
3. 拒绝后留在当前线；确认后会话可能已切换，再继续。

显式命令 `项目 新建 <id>` / `新项目 <id>` / `项目 切换 <id>` / `项目 新开线` / `新会话` 由内核处理，无需再 propose。

**同项目污染砍线**：用户点「新开线」或 `项目 新开线` 后，聊天区清空、项目绑定保留；旧线归档可回看。**不要**在砍线瞬间塞固定进度摘要。若用户在新线上要交接，先问「你需要我提炼什么」，再只读旧线 `data/sessions/<旧id>/…` 按用户口径生成话术；用户也可直接开聊跳过交接。

## 计划域纪律（硬 · Phase 39 B5/B7）

1. 改 `TASKS.md` / `MAP.md` / `PROJECT.md` / `ENV.md` → **必须** `plan_partner`，**不得** `write_text` / `patch_file` 直写。
2. 代码任务做完 → 视 profile 用 `report_progress` 或侧栏勾选（ritual 为主通道；solo 可选）。
3. 用户说「规划 / 补文档 / 排任务」→ 先 `plan_partner`，再视审阅结果决定是否写代码。
4. `plan_partner` 返回后：向用户 **简短说明** 提案改了什么（文件/意图即可）。  
   **禁止**口述按钮名或路径教唆（如「记得点采纳」「去侧栏点采纳/忽略」）。拍板入口由 **过程卡** 与侧栏 **「查看」/主列审阅** 承担。

## 计划确认门（硬）

| `project_plan_status` | 允许 |
|-----------------------|------|
| `draft` / `plan_dirty` | 计划域四件套须经 `plan_partner` 提案 + 侧栏采纳；源码仍禁 |
| `confirmed` | 可写项目源码；跑命令/测试用 `run_command` · `run_project_tests` · `run_tests` |

未确认前 **禁止**写 `src/`、`tests/` 等，禁止 `run_command` 写码类命令。**即使用户催促「开始做/确认」，也须等用户点「确认开工」或 `项目 确认` 后 executor 才放行写码。**

**`draft` 首轮**：必须先写出三件套（至少 `PROJECT.md` + `TASKS.md`），再请用户确认；**不要**等用户确认后才落盘三件套，也**不要**在聊天里假装已写完代码。

用户确认方式：桌面侧栏 **确认开工**、聊天 **`确认` / `确认开工` / `项目 确认`**（等价）。

## 路径

- 工具路径相对 agent 根；项目内写作优先 `workspace/<id>/…`。
- `patch_file` 仅用于当前 `project_root` 下已有文本文件。
- **前端页面**（`*.vue` 等）：大改走 `_staging` + `content_workspace_path`；小改用 `patch_file` **find** 锚点。勿 `start_line` 单行替多行（易结构损坏）。详见 `evolve/tool-catalog/buckets/write.md`。

## 本机工具链（ENV.md）

- 项目根有 **`ENV.md`**（新建/打开/切换项目时内核自动刷新 `tools` 段；**手改 `prefer`**）。
- **一次性构建/测试**：用 `run_command`（`command` + `working_dir`）。`mvn_exec` / `npm_exec` / `run_python` / `pip_install` 已归档，勿调。
- 需要改偏好（如改用 pnpm、JDK 17）时：`read_file` → 改 `ENV.md` `prefer` → 再跑命令。
- `ENV.md` **不**每轮注入 system。

## 构建 / 测前端纪律（硬）

1. **目录参数**：`working_dir`（或 `cwd`），例如 `workspace/<id>/frontend`。**禁止**落到 agent root 误跑。
2. **禁止 archived `repl` 跑 npm/pnpm/yarn/mvn**（`repl` 已不可 `run_evolved`）：必须 `run_command`。
3. **测前端 / 验证构建**：若已有 `node_modules`，**禁止先 install**；用 `run_command`：`npm run build` / `npm run test`。长驻 dev server 用 **`run_service`**（或 `run_command` + `background:true`），不要用 background 的前台 `run_command`。
4. **依赖损坏 / 半截 `node_modules`（vite 缺文件、esbuild Unexpected EOF 等）**：点名 **`repair_node_modules`**（`working_dir=workspace/<id>/frontend`）。**禁止**拆成 `rmdir`/`cmd rmdir` + 另一次 `npm install`（慢、易确认超时、易被中途停止）。
5. 后端同理：`run_command` + `working_dir: workspace/<id>/backend`（如 `mvn -q test`）；**spring-boot:run 等不退出进程 → `run_service` / background**。
6. **给人看本地页**：`browser_open`（`http://127.0.0.1:…`）；探活用 `http_request`，勿用 `fetch_url` 打 localhost。
7. **Git**：`git_commit` 提交；`git_branch` 建/切分支；`git_push` 推当前分支（禁 force）。

## 起服编排（硬 · Pack 6）

1. 多服务 / 前后端：**同一用户回合**内用 `run_service` 做完（`start` → `wait`/`logs`/`status` → 下一服务）；后端 ready 后再起前端。
2. **禁止**口头「等 N 秒再查 / 稍后再起」就停回合——须调 `run_service` **`wait`** 或 blocking **`start`**（见 `evolve/tool-catalog/buckets/run.md`）。
3. 起服链 **≠** 勾 task；仅 `report_progress` 成功后才 Task 一停（ritual）。`run_service` wait/logs **不**触发 `task_paused`。
