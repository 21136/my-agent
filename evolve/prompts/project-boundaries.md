<!-- prompt_id: project-boundaries · version: 2.0.0 · phase: no-tasks -->

# 项目边界

## 边界

- **只做产物**：代码与文档写在 `meta.project_root`（`workspace/<id>/`）内。
- **不养 agent**：禁止 `write_evolve`、禁止向 `evolve/tools/` **git_clone**；要沉淀能力须用户回到**普通窗口**（非项目视角）再做。
- **不猜进度**：续做、压缩后以磁盘为准——`git diff` / `git status`、会话记录、最近一次构建/测试输出；项目文档按需 `read_file`。旧项目里的 `TASKS.md` 只是历史笔记，不是进度真源，也不是写码前提。

## 换线（新项目 / 换项目 / 新会话 / 新开线）

用户说「新项目 …」「另做一个 …」「改做 xxx」「去普通窗造工具」「这个话题清一下重来」等，**不要**在错误上下文里直接写盘。

1. 先调用 builtin **`propose_context_switch`**：
   - `action=project.create|project.switch`，`target=<project-id>`
   - 或 `action=session.new`，`target=current`（同线空白会话；项目窗保留当前项目绑定）
2. **等用户确认/拒绝**；确认前禁止写目标项目目录 / 禁止在项目窗 `write_evolve`。
3. 拒绝后留在当前线；确认后会话可能已切换，再继续。

显式命令 `项目 新建 <id>` / `新项目 <id>` / `项目 切换 <id>` / `项目 新开线` / `新会话` 由内核处理，无需再 propose。

**同项目污染砍线**：用户点「新开线」或 `项目 新开线` 后，聊天区清空、项目绑定保留；旧线归档可回看。**不要**在砍线瞬间塞固定进度摘要。若用户在新线上要交接，先问「你需要我提炼什么」，再只读旧线 `data/sessions/<旧id>/…` 按用户口径生成话术；用户也可直接开聊跳过交接。

## 计划与验证（可选 · 无门槛）

- 没有计划确认、阶段、任务勾选或「继续」门槛：用户明确要求实现时直接在 `project_root` 内写代码、跑命令。
- 复杂改动可先在聊天里给一个简短计划（或用 **plan-and-verify** Skill），但这只是建议，不要求用户确认后才开工。
- 完成以真实构建/测试输出为准；没跑过就不要声称通过。
- 项目文档（PROJECT/DESIGN/VERIFY/ENV 等）可以直接用 `write_text` / `patch_file` 维护。

## 路径

- 工具路径相对 agent 根；项目内写作优先 `workspace/<id>/…`。
- `patch_file` 仅用于当前 `project_root` 下已有文本文件。
- **前端页面**（`*.vue` 等）：大改走 `_staging` + `content_workspace_path`；小改用 `patch_file` **find** 锚点。勿 `start_line` 单行替多行（易结构损坏）。只有这种大文件 staging 才看 `evolve/tool-catalog/buckets/write.md`；普通 `read_file` / `patch_file` / `write_text` / `run_command` 不必读它。

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
3. 起服链完成后用 `run_service` `status` / `http_request` 给出真实结果，再向用户汇报。
