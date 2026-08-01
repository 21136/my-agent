# 工具确认管线设计（CONFIRM-PIPELINE）

> 版本 **0.1.1** · 2026-07-30  
> **状态**：**done**（T-1301～T-1308）  
> **UI 路径**：现行实现在 `desktop/src/shells/unified/` + `chat-state.ts`（旧 grow/project/daily 路径已删；night 视角可用玻璃 overlay）  
> 关联：[DESKTOP.md](./DESKTOP.md) §0 · [BUGS.md](./BUGS.md) · [TOOLS.md](./TOOLS.md) §6.3 · `write_evolve`

---

## 0. 已决摘要

| ID | 决议 |
|----|------|
| **C1** | `confirm_fn` **禁止**在 `request_id` 不匹配时无限 `get→put` 空转；错 ID **丢弃并告警** |
| **C2** | 确认超时（`queue.Empty`）**必须** `emit confirm.done`（`choice: n`）+ `notice`，再返回 |
| **C3** | 桌面 **仅接受** `confirmOverlay.requestId`；点击后立即 `block.resolved`，重绘不得复活按钮 |
| **C4** | 新 `confirm.request` 到达时，**作废**同轮旧 confirm 块（标「已过期」） |
| **C5** | 状态栏由 **`chat-state` 钩子**驱动；unified **禁止**手写「就绪/执行中」覆盖 |
| **C6** | `ToolExecutor.run` **`try/finally`** 保证 `tool.end` 必发（含异常路径） |
| **C7** | `write_evolve` 的 `content_base64` **确认前** `b64decode(validate=True)`；失败 **不弹确认** |
| **C8** | 大文件 scaffold **优先** `content_workspace_path` / `files`；`content_base64` 仅小片段（`tool.toml` 等） |
| **C9** | `_run_line` 结束 **必发** `turn.end`（或空 `assistant.done`），对称 `turn.start` / `beginTurnActivity` |
| **C10** | **unified + pet** 共用 confirm 状态机；差异仅在呈现（块内 vs night 玻璃层 vs pet 气泡） |

---

## 1. 动机

### 1.1 问题

桌面壳自 2026-07-11 起连续修复 confirm 相关缺陷（BUG-002～007），但 **2026-07-13** 仍出现：

- 用户点「同意」后状态 **「已提交确认，执行中…」持续 30+ 分钟**
- 首轮 `write_evolve` 实际 **123ms 失败**（base64 padding 非法）
- 助手改走 `write_text` 暂存 → **第二次确认** → 会话 `messages.jsonl` 在 10:19:52 后 **不再写入**

说明：**WS 读循环解耦（BUG-002）不够**；confirm 在「多轮工具 / 失败重试 / UI 重绘」组合下仍会 **假死或谎报状态**。

### 1.2 结论

需要把 **sidecar 确认队列 + executor 工具生命周期 + 桌面状态机** 当作 **一条管线** 统一加固，而不是零散 patch。

---

## 2. 管线架构

```text
用户消息 / 工具回合
    │
    ▼
executor 需 confirm ──► WsBridge.confirm_fn
    │                      emit confirm.request (request_id)
    │                      阻塞等 confirm.response
    ▼
桌面 confirmOverlay / confirm 块
    │  用户点 y/n/a
    ▼
confirm.response ──► deliver_confirm ──► confirm_fn 返回
    │                      emit confirm.done
    ▼
tool.start ──► 执行 ──► tool.end
    │
    ▼
（可能下一轮 tool / LLM）
    │
    ▼
assistant.done / turn.end ──► 桌面 resetTurnActivity
```

**线程模型**（已定，BUG-002）：

- WebSocket `async for` 经 `create_task(_handle_incoming)` 不阻塞读循环
- `confirm.response` 走 `_dispatch_inline`，与 `TURN_LOCK` 内 `_run_line` **并行**

**脆弱点**（本设计要修）：

1. `confirm_fn` 内层 `while True` 对 **错 ID** 无退出条件
2. 桌面 **多张 confirm 块** + `innerHTML` 重绘 → 旧卡可再点
3. grow 在点击时 `setStatus("执行中")`，但 `isWorking()` 在 `confirmPending` 时为 **false**
4. `confirm.done` 时 grow 立刻 `setStatus("就绪")`，与真实 tool 执行 **不同步**

---

## 3. 状态机（桌面 · 目标）

| 阶段 | `confirmPending` | `turnActive` | `toolsRunning` | `isWorking()` | 状态栏文案（目标） |
|------|------------------|--------------|----------------|---------------|-------------------|
| 等确认 | true | 任意 | 0 | **false** | 等待确认… |
| 已点同意，等 `confirm.done` | true→false | true | 0 | true | 确认中… |
| `confirm.done` + y，工具执行前 | false | true | 0 | true | 执行中… |
| `tool.start` | false | true | +1 | true | · \<tool\> |
| `tool.end` | false | true | -1 | true/false | 工具完成/失败 |
| `assistant.done` | false | false | 0 | false | 就绪 |
| `error` / `turn.end` | false | false | 0 | false | 错误 / 就绪 |

**与现状差异**：

- 现状：点同意即「执行中」，但 `confirmPending=true` 时 `isWorking=false`（全窗 busy 可能不亮）
- 现状：`confirm.done` 立刻「就绪」，工具尚未 `tool.start`

---

## 4. 开放缺陷清单（实施前）

| ID | 严重度 | 现象 | 根因位置 | 对应决议 |
|----|--------|------|----------|----------|
| **BUG-008** | P0 | 点确认后半小时无响应；会话不落盘 | `server.py` `confirm_fn` 错 ID 空转 | C1, C2 |
| **BUG-008b** | P0 | 超时后工具跳过但 UI 仍 `confirmPending` | `confirm_fn` 超时无 `confirm.done` | C2 |
| **BUG-009** | P1 | 旧确认卡可再点 → 错 ID | `grow/index.ts` `renderChat` 重绘 | C3, C4 |
| **BUG-010** | P1 | 状态「执行中」与 busy /chrome 不一致 | `chat-state.ts` `isWorking` + grow 手写 status | C5 |
| **BUG-011** | P1 | `tool.end` 缺失 → 永久 working | `executor.py` `run` 无 finally | C6 |
| **BUG-012** | P1 | 空回复无 `assistant.done` | `main.py` `_run_line` 条件 emit | C9 |
| **BUG-013** | P1 | base64 确认后才失败 → 多轮确认雪崩 | `executor.py` + `write_evolve` | C7, C8 |

详见 [BUGS.md](./BUGS.md) 索引与 `docs/bugs/` 单篇。

---

## 5. Sidecar 协议修订

### 5.1 `confirm.request`（不变）

```json
{ "type": "confirm.request", "request_id": "<uuid>", "preview": "…", "allow_approve_all": true }
```

### 5.2 `confirm.done`（加强）

**每次** `confirm_fn` 返回前 **必须** 发（含超时、错 ID 中止）：

```json
{ "type": "confirm.done", "request_id": "<uuid>", "choice": "y|n|a|timeout|stale" }
```

| `choice` | 含义 |
|----------|------|
| `y` / `n` / `a` | 用户选择（现有） |
| `timeout` | 3600s 无响应（C2）→ **Phase 15 改为 `CONFIRM_TIMEOUT_SEC` 默认 90s** |
| `stale` | 服务端丢弃的过期 response（可选，调试用） |
| `cancelled` | 用户 Stop（`turn.cancel` · Phase 15 R3） |

### 5.3 `turn.end`（新增 · C9）

回合线程退出时（含异常、无 assistant 文本）：

```json
{ "type": "turn.end", "ok": true, "finish_reason": "completed|error|cancelled" }
```

桌面收到后 **`resetTurnActivity()`**，与 `assistant.done` 并列。

### 5.4 `confirm.stale`（可选 · C4）

新 `confirm.request` 发出时，通知桌面作废上一张卡：

```json
{ "type": "confirm.stale", "request_id": "<old-uuid>" }
```

M0 可仅用前端「新 request 到达 → 标记旧块 resolved=已过期」实现，不必单独 WS。

---

## 6. `write_evolve` / scaffold 脆弱路径

与 confirm 管线耦合的 **高频失败** 模式：

| 步骤 | 风险 | 加固 |
|------|------|------|
| LLM 输出 `content_base64` | 截断 / padding / 非 4 倍数 → 123ms 失败 | C7 确认前校验 |
| 失败后改 `write_text` → `_staging` | **第二次 confirm** | C3/C4 防旧卡 |
| 再 `write_evolve` + `content_workspace_path` | **第三次 confirm** | 文档 + prompt 优先 staging |
| 多文件 `files{}` | 每文件一次 confirm | 非本 Phase 范围；先减 base64 雪崩 |

**Prompt 侧**（T-1307）：`core.txt` / `coding.md` 明确 — 大 `main.py` **先** `write_text` 到 `workspace/_staging*.py`，再 `content_workspace_path`，**勿**对大段代码用 `content_base64`。

---

## 7. 模块与改动面

| 路径 | 职责 |
|------|------|
| `agent-core/server.py` | `confirm_fn` C1/C2；`_run_line` 发 `turn.end` C9 |
| `agent-core/tools/executor.py` | `run` finally `tool.end` C6；base64 预检 C7 |
| `agent-core/main.py` | 空 `assistant_text` 仍结束回合 C9 |
| `desktop/src/shells/chat-state.ts` | `isWorking` / `confirmSubmitting` C5 |
| `desktop/src/shells/unified/index.ts` | confirm 块内 + night overlay；C3/C5 |
| `desktop/src/shells/chat-state.ts` | 确认状态机共用 |
| `desktop/src/shells/pet/index.ts` | 气泡内确认 |
| `desktop/src/shells/pet/index.ts` | 同 daily |
| `agent-core/tests/test_confirm_pipeline.py` | 新增：错 ID、超时、双 confirm |

---

## 8. 里程碑（Phase 14）

| ID | 交付 | 状态 |
|----|------|------|
| T-1301 | 本文档评审 | **done** |
| T-1302 | `confirm_fn` C1+C2 + 单测 | **done** |
| T-1303 | 桌面 C3+C4+C5（grow + project） | **done** |
| T-1304 | daily + pet confirm 对齐 | **done** |
| T-1305 | executor C6 + C7 | **done** |
| T-1306 | `turn.end` C9 + 三壳处理 | **done** |
| T-1307 | scaffold prompt / TOOLS 修订 C8 | **done** |
| T-1308 | 集成：`write_evolve` 失败 → 二次 confirm 不卡死 | **done** |

**M0 完成标志**：复现 BUG-008 路径（坏 base64 → 第二次 confirm）→ 点同意 → **60s 内** 必达 `confirm.done` + `tool.end` 或 `turn.end`；错点旧卡 → `notice` + 不卡线程。

---

## 9. 验证

### 9.1 自动化

```powershell
Set-Location D:\my-agent\agent-core
python -m pytest tests/test_confirm_pipeline.py -q
python server.py --demo
```

`server.py --demo` 应含 `[PASS] confirm stale id does not spin`（T-1302 后）。

### 9.2 手工（grow 壳）

1. 触发 `write_evolve` + 故意坏 base64（或等模型失败）
2. 助手发起 `write_text` 暂存 → **第二张** 确认卡
3. 点 **新卡** 同意 → 应在数秒内 `tool.end` + 继续或 `assistant.done`
4. 重试：点 **旧卡**（若仍可见）→ 不应卡死；应有告警 notice

### 9.3 回归

- BUG-002：confirm 仍走 inline，不回归 WS 死锁
- BUG-005：回合中断后 `repair_orphaned_tool_calls` 仍有效
- BUG-007：`新会话` 后 `resetTurnActivity` 仍有效

---

## 10. 非目标

- 取消 `write_evolve` 逐次 confirm（`workspace_only=false` 仍无 session `a`）
- 多文件 scaffold **一次确认**（未来 `batch_write_evolve`）
- CLI `input()` confirm 大改（仅保证不 spin）

---

## 11. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | 2026-07-13 | 初稿：BUG-008 事件；C1–C10；Phase 14 任务 |
