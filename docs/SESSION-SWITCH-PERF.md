# 项目会话切换性能（SESSION-SWITCH-PERF）

> 版本 **0.1.0** · 2026-09-02 · **UI-5972**  
> 关联：[UX-POLISH.md](./UX-POLISH.md) **UX-027** · [DESKTOP.md](./DESKTOP.md) §5.2.1 · [TASKS.md](./TASKS.md) UI-5972

---

## 0. 问题

用户反馈：Desktop **切换项目会话**（如 `private-code` 1036 条 ↔ `music` 1266 条）**卡数分钟**，像「没换过去」。

对标 **Cursor**：切换 Chat 以「换一条线 + 轻量加载」为主；**展示历史 ≠ 全量重建项目态 ≠ 重复 round-trip**。

---

## 1. 根因（2026-09-02 排查）

| 层级 | 瓶颈 |
|------|------|
| **前端** | `session.history` 到达后，对每条 assistant 同步 `renderMarkdown` + mermaid（主线程） |
| **前端** | `project.switch.done` 后曾再 `refreshSession()` + `refreshProject()` → **重复** history / state / token 估算 |
| **前端** | `session.banner` + `project.state` 各触发一次 `refreshProjectThreads()` |
| **后端** | `perform_project_switch` 打包 `project.state` + `build_state` ×2 + `session_memory_event`（全量 token 估算） |
| **后端** | `list_session_summaries` 对每个会话 **读完整** `messages.jsonl`（UX-020 意图为 tail 读，未落地） |
| **后端** | `session.history` 无上限，1000+ 条 user/assistant 一次 WS 推送 |

---

## 2. 已决方案（UI-5972）

| ID | 决议 | 状态 |
|----|------|------|
| **SP-1** | 切换完成 **不再** `refreshSession` / `refreshProject`（switch 事件包已含 state/history） | **M0 done** |
| **SP-2** | `list_session_summaries`：**首尾 tail 读** jsonl + 换行计数条数，不全量 `read_text` | **M1** |
| **SP-3** | `session.history` **默认最近 200 条**；载荷带 `truncated` / `omitted_count`；前端 notice | **M2** |
| **SP-4** | 同批 switch 事件 **不重复** `build_state`（`plan.state` 已发则 skip clear 内 rebuild） | **M3** |
| **SP-5** | `refreshProjectThreads` **合并**同 tick 多次调用 | **M0 done** |
| **SP-7** | 切换时 **不加载** messages.jsonl（`message_cap=0`）；history 流式读盘；`session.memory` quick 估 token | **M5 done** |
| **SP-9** | `session.open` / 会话线切换走延迟加载 + `asyncio.to_thread`；去掉 open 后全量 `session.list` | **M6 done** |

**状态：M0～M6 done（2026-09-02）**

### 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `MY_AGENT_SESSION_HISTORY_MAX_ITEMS` | `200` | Desktop `session.history` 最多条数；`0` = 不限制 |
| `MY_AGENT_SWITCH_MESSAGE_CAP` | `0` | 切换 load_session 时载入 jsonl 条数；`0` = 延迟到首条消息再全量加载 |

---

## 3. 实现映射

| 模块 | 文件 |
|------|------|
| M0 去重 refresh | `desktop/src/shells/unified/index.ts` |
| M1 tail 读 | `agent-core/session.py` · `_extract_user_messages` |
| M2 history 窗口 | `session.py` · `build_session_chat_history` · `session_history_event` |
| M2 前端 notice | `desktop/src/shells/chat-state.ts` · `ws.ts` |
| M3 plan state 去重 | `agent-core/project_api.py` · `_clear_plan_chat_events` |
| M4 懒 markdown | `desktop/src/lazy-markdown.ts` · `unified/index.ts` |
| M5 延迟加载 + quick memory | `session.py` · `context.py` · `project_switch.py` |
| M5 threads 合并 | `unified/index.ts` · `refreshProjectThreads` · debounced list* |

---

## 4. 验收

| ID | 步骤 | 期望 |
|----|------|------|
| **S-5972a** | `private-code` ↔ `music` 切换（各 1000+ 条） | 较改前明显缩短；**无**双次全量 history 渲染 |
| **S-5972b** | 会话 >200 条 user/assistant | `session.history` 带 `truncated: true`；聊天区顶部 notice |
| **S-5972c** | 打开会话 overlay / 项目线程列表 | `list_session_summaries` 不读完整 10MB jsonl（IT-5972） |
| **S-5972d** | 切换 1000+ 条会话后向上滚动 | 视口外助手消息为纯文本；滚入视口后渲染 markdown |
| **IT-5972a** | 大文件 tail 提取 | 首尾 user 正确 · `message_count` 正确 |
| **IT-5972b** | history cap | 250 条 → 返回 200 · `omitted_count=50` |

---

## 5. 变更记录

| 版本 | 日期 | 说明 |
|------|------|------|
| 0.1.0 | 2026-09-02 | 初稿 · UI-5972 M0～M3 |
| 0.1.1 | 2026-09-02 | M4 懒 markdown · P2 狂奔 harness sync |
