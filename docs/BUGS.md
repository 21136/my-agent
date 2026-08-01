# Bug 修复记录

> 记录已修复的运行时缺陷，便于复盘与避免同类回归。  
> 每条详情见 [`docs/bugs/`](./bugs/) 下按日期命名的文件。  
> **UI 路径注记（2026-07-30）**：历史 bug 文里的 `shells/grow|daily|project` 指合并前路径；现行聊天 UI = `shells/unified/`。  
> **稳定化专项**（Phase 18）：见 [STABILIZATION.md](./STABILIZATION.md) · [stabilization-log.md](./stabilization-log.md) · [stabilization-backlog.md](./stabilization-backlog.md)  
> **T-1808-bug-04（2026-07-18）**：索引 BUG-001～019 **全部 fixed**。  
> **T-1808-bug-05（2026-07-18）**：扫描时唯一 open P1 为 backlog STD-001 → 已迁 **[BUG-020](./bugs/2026-07-18-shell-sessions-park-pollution.md)**（**fixed**）。  
> **2026-07-30**：**[BUG-021](./bugs/2026-07-30-project-progress-deadlock.md)** P0 — 项目进度闭环死结；**2026-07-31 fixed**（Phase 21 / [PROJECT-MODE §0e](./PROJECT-MODE.md)）。

---

## 索引

| 日期 | ID | 现象 | 根因位置 | 状态 |
|------|-----|------|----------|------|
| 2026-07-11 | [BUG-001](./bugs/2026-07-11-toolerror-summary-get.md) | 桌面壳聊天显示 `'ToolError' object has no attribute 'get'` | `agent-core/tools/executor.py` `_tool_result_summary` | fixed |
| 2026-07-11 | [BUG-002](./bugs/2026-07-11-confirm-button-deadlock.md) | 工具确认「同意/拒绝」点击无反应 | `agent-core/server.py` WS 读循环被回合 `await` 阻塞（二次修复：`create_task`） | fixed |
| 2026-07-11 | [BUG-003](./bugs/2026-07-11-dev-server-exit-on-electron-crash.md) | 提问后 dev 进程退出（`没有找到进程`） | `vite-plugin-electron` 默认 `process.exit` | fixed |
| 2026-07-11 | [BUG-004](./bugs/2026-07-11-streaming-error-read.md) | 聊天显示 `Attempted to access streaming response content…` | `llm_client.py` 流式错误体未 `read()` | fixed |
| 2026-07-11 | [BUG-005](./bugs/2026-07-11-orphaned-tool-calls.md) | `insufficient tool messages following tool_calls` | 确认/崩溃后会话历史残缺 | fixed |
| 2026-07-11 | [BUG-006](./bugs/2026-07-11-turn-lock-deadlock.md) | 发消息后一直「处理中…」无响应 | `server.py` `TURN_LOCK` 嵌套获取死锁 | fixed |
| 2026-07-13 | [BUG-007](./bugs/2026-07-13-new-session-ui-stuck.md) | 聊天框输入「新会话」后一直「处理中…」 | `user.message` 未 `emit_session_state`；前端未 `resetTurnActivity` | fixed |
| 2026-07-13 | [BUG-008](./bugs/2026-07-13-confirm-pipeline-stuck.md) | 点「同意」后长时间「执行中…」/ 回合不落盘 | `server.py` `confirm_fn` 错 `request_id` 无限空转 | **fixed** |
| 2026-07-13 | [BUG-009～013](./bugs/2026-07-13-confirm-ui-status.md) | 旧确认卡可再点、状态谎报、`tool.end` 漏发、base64 确认后失败 | grow/project · `executor` · `chat-state` | **fixed** |
| 2026-07-13 | [BUG-014](./bugs/2026-07-13-turn-stall-no-stop.md) | 「思考中…」10+ 分钟；无 Stop；confirm 3600s | `server.py` · 无 `turn.cancel` | **fixed**（S-05 / S-26 / S-28） |
| 2026-07-14 | [BUG-015](./bugs/2026-07-14-sidecar-startup-ws-sender.md) | sidecar 启动即退出 `AgentPaths is not defined` | `server.py` 缺 `from paths import AgentPaths` | **fixed** |
| 2026-07-14 | [BUG-016](./bugs/2026-07-14-sidecar-startup-ws-sender.md) | pet/工作台切换后 `[sidecar] connection handler failed` | `server.py` `_sender` 未捕 `ConnectionClosed`；共用 `_outbox` | **fixed** |
| 2026-07-14 | [BUG-017](./bugs/2026-07-14-guard-event-log-crash.md) | `write_text` 超内联上限后 `guard_type` 重复传参崩溃 | `executor.py` `_record_guard_event` | **fixed** |
| 2026-07-14 | [BUG-018](./bugs/2026-07-14-scaffold-filename-guard.md) | `write_text` 写 `workspace/.../README.md` 被误拦为 evolved 脚手架 | `executor.py` `_validate_scaffold_evolved_call` 仅按 basename | **fixed** |
| 2026-07-14 | [BUG-019](./bugs/2026-07-14-project-switch-import.md) | 项目切换蓝条 `cannot import name 'session_memory_event' from 'session'` | `project_api.py` 错从 `session` 导入 `session_memory_event` | **fixed** |
| 2026-07-18 | [BUG-020](./bugs/2026-07-18-shell-sessions-park-pollution.md) | grow Q&A 后 `shell_sessions.daily` 被写成 grow 会话（串线） | `park_session` × `activity_router` / `agent.py`（STD-001） | **fixed** |
| 2026-07-30 | [BUG-021](./bugs/2026-07-30-project-progress-deadlock.md) | 确认后无法勾选 TASKS：`report_progress` 不在清单 + 直写被拦 + 一停不武装 | allowlist scope≠coding · `agent.run_turn` draft→grow · task_stop 武装面 | **fixed** |

---

## 桌面壳联调：常见现象速查

| 你看到的 | 对应 ID | 处理 |
|----------|---------|------|
| `'ToolError' object has no attribute 'get'` | BUG-001 | 重启 `start-desktop.bat`（已修） |
| grow 聊一句后切 daily / 伴侶，历史串成 grow | BUG-020 | 重启 `start-desktop.bat`（已修）；可核对 `data/state.json` · `shell_sessions` grow≠daily |
| 说「新项目 X」仍挂旧项目；「项目 确认」确认错项目 | （产品缺口） | 绕行：聊天输入 `项目 新建 <id>` 或侧栏切换；设计 [CONTEXT-SWITCH.md](./CONTEXT-SWITCH.md) Phase 19 |
| 点「同意/拒绝」无反应，一直「等待确认…」 | BUG-002 | **完全退出后重启**；点同意后应出现「已提交确认，执行中…」 |
| 终端 `没有找到进程` + `请按任意键继续` | BUG-003 | 重启 dev；Electron 闪退会自动拉起 |
| `Attempted to access streaming response content` | BUG-004 | 重启后看**真实** API 错误（多为 Key/配额） |
| `insufficient tool messages following tool_calls` | BUG-005 | 重启续聊（自动 repair）；或输入 `新会话` |
| 发消息后一直「处理中…」，半小时无动静、无任何 `turn.start` | BUG-006 | **完全退出后重启** `start-desktop.bat`（已修） |
| 聊天框输入「新会话」后一直「处理中…」，后端已跑完 | BUG-007 | 重启 `start-desktop.bat`（已修）；状态应秒回「就绪」 |
| 点「同意」后半小时「执行中…」；`write_evolve` 已秒失败 | BUG-008 | 重启加载修复；只点**最新**确认卡 |
| 多张「工具确认」卡；点旧卡后彻底卡死 | BUG-008 + BUG-009 | 旧卡会标「已过期」；错 ID 不再卡线程 |
| 「思考中…」10+ 分钟，杀 python 才能恢复 | BUG-014 | **已修**；忙时点 **停止**（`turn.cancel`）；confirm 默认 90s 超时 |
| `Failed to start Python sidecar` · `AgentPaths is not defined` | BUG-015 | 重启 `start-desktop.bat`（已修） |
| `[sidecar] connection handler failed` · `ConnectionClosedOK` 1005 | BUG-016 | 多为切窗/重连；已静默处理；可忽略 `libpng iCCP` |
| `log_guard_event() got multiple values for keyword argument 'guard_type'` | BUG-017 | 已修；大文件走 `_staging` + `content_workspace_path` |
| `README.md` 属于 evolved 工具脚手架，不能经 write_text`（在 workspace 项目里） | BUG-018 | 已修；`workspace/**/README.md` 可正常 `write_text` |
| `cannot import name 'session_memory_event' from 'session'`（切项目时） | BUG-019 | 已修；重启 `start-desktop.bat` 后侧栏切换应正常灌 `session.history` |
| 助手说「report_progress 不在清单」/ 不能勾 TASKS；或提议新造该工具 | BUG-021 | **已修**；重启 sidecar；须用 `report_progress` 勾选，勿直写 TASKS |
| 拒确认/测试未跑仍勾验收；同 turn 连勾多项 | — | **设计中** [PROGRESS-GATE.md](./PROGRESS-GATE.md) Phase 24；现网仍可能发生 |

**改代码后务必**：关掉托盘/Electron → 重新 `start-desktop.bat`（Python sidecar + Vite 均加载新逻辑）。

---

## 如何新增一条

1. 在 `docs/bugs/` 新建 `YYYY-MM-DD-<简短 slug>.md`
2. 在上表追加一行（链到该文件）
3. 在 `CHANGELOG.md` 顶部补 `[0.2.x]` 修订（代码可见行为变化时）
4. 若涉桌面壳协议/生命周期，同步 `DESKTOP.md` 已决/修订记录

### 模板

```markdown
# BUG-NNN：<标题>

- **日期**：YYYY-MM-DD
- **发现于**：桌面壳 / CLI / …
- **严重度**：P0 | P1 | P2
- **根因类**：A 接线 | B 异步 | C 生命周期 | D 规则误伤 | E 完成定义松（见 STABILIZATION §1）
- **覆盖面**：STABILIZATION §3 哪一行 / 建议 S-xx · IT-xx
- **状态**：fixed | open | wontfix

## 现象（最少复现步骤）

## 根因

## 修复

## 验证（自动化或 smoke ID）

## 预防
```
