# 回合控制设计（TURN-CONTROL）

> 版本 **0.2.1** · 2026-07-30  
> **状态**：**implemented**（T-1401～T-1408；Stop / Escape · UX-002）  
> **UI 路径**：`shells/unified/` + `shells/pet/`（旧 grow/project/daily 路径已删）  
> 关联：[CONFIRM-PIPELINE.md](./CONFIRM-PIPELINE.md) · [DESKTOP.md](./DESKTOP.md) §0 · [UX-POLISH.md](./UX-POLISH.md) · [BUGS.md](./BUGS.md) · [TASKS.md](./TASKS.md) §Phase 15

---

## 0. 已决摘要

| ID | 决议 |
|----|------|
| **R1** | **unified / pet** 在 **`isWorking()`** 时展示 **「停止」**（Escape 亦可）；点击即发 `turn.cancel`，**不**走 `TURN_LOCK` |
| **R2** | `turn.cancel` **必须** `_dispatch_inline` 处理（与 `confirm.response` 同级），回合线程阻塞时仍可送达 |
| **R3** | 取消后 sidecar **必发** `turn.end`（`finish_reason: cancelled`）+ 若 pending confirm 则 `confirm.done`（`choice: cancelled`） |
| **R4** | `confirm_fn` 单次 `queue.get` 超时从 **3600s** 改为 **`CONFIRM_TIMEOUT_SEC`（默认 90s）**；与 `LLM_TIMEOUT_SEC`（120s）同量级 |
| **R5** | LLM 流式请求须 **可协作取消**：`WsBridge` 持 `threading.Event`；`llm_client` 消费 SSE 时轮询；取消 → 关连接或抛 `LLMCancelledError` |
| **R6** | 取消不删已落盘 `messages.jsonl`；当前轮 **未写完** 的 assistant / tool 由既有 `repair_orphaned_tool_calls` 在下次 `Session.load` 修补 |
| **R7** | 收到取消后的 `turn.end` 时桌面 **`resetTurnActivity()`**；状态栏 **「已停止」**（2s 后回「就绪」）；composer 恢复输入 |
| **R8** | **P1 defer → Phase 16**：无事件看门狗；内联写入上限 — 见 [RUNTIME-GUARDS.md](./RUNTIME-GUARDS.md) · §5（原 T-1410～T-1412） |

---

## 1. 动机

### 1.1 问题（Phase 14 之后仍会发生）

| 现象 | 日志 / 证据 | Phase 14 是否覆盖 |
|------|-------------|-------------------|
| 底部「思考中…」10+ 分钟 | `messages.jsonl` 长时间无写入；`reasoning.delta` 偶发后静默 | 否 — 无 Stop |
| 确认卡「提交中…」不结束 | 用户已点同意；`confirm.done` 未到或线程仍在 LLM | 部分 — C1/C2 修空转，但超时仍 3600s |
| 同会话反复重试大段代码 | 历史膨胀；`deepseek-v4-pro` + coding 主题 | 否 — prompt C8 仅软约束 |
| 只能杀托盘 / 杀 python | 无产品内「停止本轮」 | 否 |

### 1.2 对标 Cursor（产品层，非实现细节）

| Cursor | my-agent 现状 | Phase 15 目标 |
|--------|---------------|---------------|
| 生成时 **Stop** 始终可见 | 无 | R1 |
| 请求级超时 + 明确报错 | LLM 120s；确认 3600s | R4 |
| 大内容走路径引用 | `write_evolve` / 整段 JSON | P1（§9） |
| 卡住可继续聊 | 同会话「继续」易越卡越肿 | R6 + 用户教育「新会话」 |

### 1.3 结论

Phase 14 解决 **「管线撒谎 / confirm 空转」**；Phase 15 解决 **「用户无法打断 + 等待时间过长」** — 两条管线正交，均须具备。

---

## 2. 架构

```text
用户点「停止」
    │
    ▼
desktop ── turn.cancel ──► _dispatch_inline（不经 TURN_LOCK）
    │
    ▼
WsBridge.cancel_turn()
    ├── cancel_event.set()
    ├── 若 _pending_confirm_id：注入 (id, "n") 或专用 cancelled
    └── 若 LLM 流：httpx 连接关闭 / 读循环退出
    │
    ▼
工作线程 repl.handle_line 退出（CancelledError 或正常收尾）
    │
    ▼
emit turn.end { finish_reason: "cancelled" }
    │
    ▼
desktop resetTurnActivity() · 状态「已停止」
```

**与 confirm 管线关系**（Phase 14 不变部分）：

- `confirm.response` 仍 inline
- `confirm.done` 与 `confirm_fn` 返回仍成对（含 `timeout` · **新增 `cancelled`**）
- `tool.end` finally 仍必发（取消路径若工具已 `tool.start` 须补 `tool.end`）

---

## 3. WebSocket 协议

### 3.1 客户端 → 服务端

#### `turn.cancel`（新增 · R1/R2）

```json
{ "type": "turn.cancel" }
```

| 字段 | 说明 |
|------|------|
| （无 payload） | 取消 **当前连接** 上 in-flight 的 `_run_line` / `repl.handle_line` |

**语义**：

- 幂等：无 in-flight 回合时 `notice`「当前无进行中的回合」
- **不**取消 proposal 检查点、project.switch 等独立协议（后续可扩展 `scope`）
- **不**杀 sidecar 进程

### 3.2 服务端 → 客户端

#### `turn.end`（扩展 `finish_reason` · 已有 C9）

```json
{ "type": "turn.end", "ok": false, "finish_reason": "cancelled" }
```

| `finish_reason` | 含义 |
|-----------------|------|
| `completed` | 正常结束（现有） |
| `error` | 异常（现有） |
| `cancelled` | 用户 Stop 或 REPL Ctrl+C 映射（**新增**） |

#### `confirm.done`（扩展 `choice`）

```json
{ "type": "confirm.done", "request_id": "<uuid>", "choice": "cancelled" }
```

| `choice` | 含义 |
|----------|------|
| `cancelled` | 用户 Stop 时作废 pending confirm（**新增**） |

#### `turn.cancelled`（可选 · M0 可省略）

```json
{ "type": "turn.cancelled", "accepted": true }
```

立即 ACK，便于 UI 在 `turn.end` 前显示「正在停止…」。**M0 可仅用本地 optimistic UI**。

### 3.3 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `CONFIRM_TIMEOUT_SEC` | `90` | `confirm_fn` 单次等待上限（R4） |
| `LLM_TIMEOUT_SEC` | `120` | 现有；保持不变 |
| `TURN_STALL_SEC` | `180` | **P1** 无 WS 事件看门狗（§9.2） |

文档同步：[MAP.md](./MAP.md) env 表 · [RUNTIME.md](./RUNTIME.md)（若已有 timeout 节）。

---

## 4. Sidecar 实现要点

### 4.1 `WsBridge`（`server.py`）

```python
@dataclass
class WsBridge:
    cancel_event: threading.Event = field(default_factory=threading.Event)
    _turn_busy: threading.Event = ...
    _pending_confirm_id: str | None = None

    def request_cancel(self) -> None:
        self.cancel_event.set()
        # 唤醒 confirm_fn：向 _confirm_queue 放入 (pending_id, "n") 或 sentinel
        # 关闭当前 httpx stream（见 4.3）

    def clear_cancel(self) -> None:
        self.cancel_event.clear()
```

- 每轮 `_run_line` **入口** `clear_cancel()`，**出口** `finally` 仍发 `turn.end`
- `_dispatch_inline` 增加 `turn.cancel` → `bridge.request_cancel()`

### 4.2 `confirm_fn`（R3/R4）

```python
def confirm_fn(self, preview, allow_approve_all) -> str:
    ...
    while True:
        if self.cancel_event.is_set():
            self.emit(confirm.done, choice="cancelled")
            return "n"
        try:
            resp_id, choice = self._confirm_queue.get(timeout=min(1.0, remaining))
        except queue.Empty:
            if elapsed >= CONFIRM_TIMEOUT_SEC:
                # 现有 C2 timeout
                ...
```

要点：

- 循环内 **轮询 `cancel_event`**（≤1s 粒度），避免 Stop 后仍等满 90s
- 总等待上限 **`CONFIRM_TIMEOUT_SEC`**，非 3600s

### 4.3 LLM 取消（R5）

| 方案 | 做法 | 取舍 |
|------|------|------|
| **A（M0 推荐）** | `LLMClient.chat` 接受 `cancel_event: Event | None`；`_consume_sse_stream` 每 chunk 检查 | 改动小；取消延迟 ≤1 chunk |
| B | `httpx` 请求放子线程，`cancel` 时 `client.close()` | 更干净；线程模型复杂 |
| C | 仅设 flag，等 120s 自然超时 | 不满足 R1 体验 |

**M0 采用 A**；取消时抛 `LLMCancelledError`，`agent.run_turn` 捕获 → 空 assistant + 结束回合。

`ConversationRepl` / `Agent` 构造时将 `bridge.cancel_event` 传入 `llm.chat(..., cancel_event=...)`。

### 4.4 工具执行中途取消

| 阶段 | 行为 |
|------|------|
| `confirm_fn` 等待 | `cancelled` → 返回 `n`，不执行工具 |
| `executor.run` 已 `tool.start` | 子进程 **不**强杀（M0）；保持「正在停止…」，等 `policy.timeout_sec` 自然结束或返回后 `tool.end` + `turn.end` |
| `subprocess` 长任务 | **P1**：`Popen` + cancel 时 `terminate`（仅 `run_python` / `npm_exec` 等） |

M0 接受：取消后极少数工具仍跑完当前子进程；为避免同一 session 并发写，**UI 只在旧回合真实 `turn.end` 后释放**。子进程立即终止列入 T-1412。

---

## 5. 桌面实现要点

### 5.1 Stop 按钮（R1/R7）

| 壳 | 位置 | 可见条件 |
|----|------|----------|
| grow | composer 行，`发送` 左侧 | `chat.isWorking()` |
| project | 同 grow | 同上 |
| daily | 胶囊输入旁 | 同上 |
| pet | 展开态输入旁 | 同上 |

**交互**：

1. 点击 → 本地 `beginTurnActivity` 保持 true，状态 **「正在停止…」**
2. `client.send({ type: "turn.cancel" })`
3. 收到 `turn.end`（`cancelled`）→ `resetTurnActivity()`，恢复 composer，状态 **「已停止」** → 2s 后 **「就绪」**
4. 重复点击：忽略（debounce）

**样式**：与 `发送` 并列次要按钮；`prefers-reduced-motion` 无动画。

### 5.2 `chat-state.ts`

| 变更 | 说明 |
|------|------|
| `onTurnCancelled?` 钩子 | 各壳更新状态栏 |
| `turn.end` + `finish_reason===cancelled` | 同 `resetTurnActivity`；`confirmSubmitting=false` |
| `confirm.done` + `choice===cancelled` | 标 confirm 块「已取消」 |

### 5.3 `api/ws.ts`

- `ClientMessage` 增加 `turn.cancel`
- `ServerEvent` 可选 `turn.cancelled`
- `AgentWsClient.sendTurnCancel(): void`

### 5.4 与 confirm 状态机叠加

Phase 14 状态表仍成立；新增行：

| 阶段 | `isWorking()` | 状态栏 |
|------|---------------|--------|
| 用户点 Stop | true | 正在停止… |
| `turn.end` cancelled | false | 已停止 → 就绪 |

**Stop 与确认卡**：点 Stop 后 pending confirm 块标 **「已取消」**，按钮 disabled。

---

## 6. 会话与数据

### 6.1 落盘（R6）

| 时机 | 磁盘状态 |
|------|----------|
| 取消于 LLM 流式中 | 可能无新 assistant 行 — 正常 |
| 取消于 assistant 已写 `tool_calls`、工具未跑 | orphan — `Session.load` repair |
| 取消于工具执行中 | 可能有 `tool` 行或 orphan — repair |

**不**在取消时自动截断或回滚 `messages.jsonl`（避免与 Git 真源冲突）；用户可 `新会话` 或手动清理。

### 6.2 用户可见 notice

```
回合已停止（未完成的工具调用将在下次加载时标记为已中断）
```

---

## 7. 模块与改动面

| 路径 | 职责 |
|------|------|
| `docs/TURN-CONTROL.md` | 本文档 |
| `agent-core/server.py` | `turn.cancel` inline；`request_cancel`；`confirm_fn` R4；`_run_line` finally |
| `agent-core/llm_client.py` | `cancel_event` 轮询；`LLMCancelledError` |
| `agent-core/agent.py` | 捕获取消，结束 tool loop |
| `agent-core/main.py` | `handle_line` 取消传播（若需） |
| `agent-core/tests/test_turn_cancel.py` | 新增：cancel during confirm / mock LLM |
| `desktop/src/api/ws.ts` | 协议类型 |
| `desktop/src/shells/chat-state.ts` | `turn.end` cancelled · confirm cancelled |
| `desktop/src/shells/unified/index.ts` | Stop / Escape |
| `desktop/src/shells/pet/index.ts` | Stop（伴侣窗） |
| `desktop/src/shells/pet/index.ts` | 展开态 Stop |
| `docs/DESKTOP.md` | §3.2.3 Stop · §5.1 协议表 |
| `docs/CONFIRM-PIPELINE.md` | §5.2 补充 `choice: cancelled`；超时默认 90s 注记 |
| `docs/MAP.md` | Phase 15 行 · env 表 |
| `docs/BUGS.md` | BUG-014 索引 |

---

## 8. 里程碑（Phase 15）

| ID | 交付 | 依赖 | 状态 |
|----|------|------|------|
| T-1401 | 本文档评审定稿 | BUG-014 | **done** |
| T-1402 | `turn.cancel` 协议 + `_dispatch_inline` | T-1401 | **done** |
| T-1403 | `WsBridge.request_cancel` + `confirm_fn` R4/R3 | T-1402 | **done** |
| T-1404 | `llm_client` 协作取消 R5 | T-1402 | **done** |
| T-1405 | 四壳 Stop 按钮 + `chat-state` R7 | T-1402 | **done** |
| T-1406 | `agent.py` 取消传播 + orphan 不恶化 | T-1403,T-1404 | **done** |
| T-1407 | `test_turn_cancel.py` + `server.py --demo` 条目 | T-1403,T-1404 | **done** |
| T-1408 | 文档：`CONFIRM-PIPELINE` / `DESKTOP` / `MAP` 同步 | T-1401 | **done** |

**后续已迁入 Phase 16**（本文档 §9 保留背景；实现规范以 [RUNTIME-GUARDS.md](./RUNTIME-GUARDS.md) v0.2.0 为准）：

| ID | 交付 |
|----|------|
| T-1410 → T-1510 | stall 看门狗（默认关闭；opt-in 180s） |
| T-1411 → T-1511 | executor 内联写入上限 `WRITE_INLINE_MAX_CHARS=8192` |
| T-1412 → T-1512 | evolved 子进程取消（terminate → 3s → kill） |

**M0 完成标志**：

1. grow 在 **LLM / confirm 等待**时点 **停止** → **3s 内** `turn.end`（`cancelled`）+ 可再输入（已进入同步工具子进程则待工具退出；T-1412）  
2. 确认等待中点 **停止** → `confirm.done`（`cancelled`）+ 不卡线程  
3. `CONFIRM_TIMEOUT_SEC=5` 演示：无操作 → **5s 内** `confirm.done`（`timeout`）  
4. `python tests/test_turn_cancel.py` PASS；`server.py --demo` 含 turn cancel PASS  

---

## 9. Phase 16 背景附录

### 9.1 内联写入上限（T-1511 · 对标 Cursor 路径引用）

**不冲突** — 项目里已有多种「长度」约束，**管的是不同层**；T-1411 只补 **写入参数进 `tool_calls` 历史** 这一层，且与现规 **叠加** 而非替换。

#### 9.1.1 现有分层（已决 / 已实现）

| 层 | 阈值 | 位置 | 管什么 |
|----|------|------|--------|
| **L1 结构** | plain `content`：**换行 / `"` / len>240** | `executor._write_evolve_content_guard` | `write_evolve` 写 `main.py` / `README.md` 时 **禁止** 在 JSON 里塞多行字面量（防 `tool_calls` 转义炸） |
| **L1b** | `tool.toml` **禁止** plain `content` | 同上 | 必须 `content_base64` 或 `content_workspace_path` |
| **L2 输出** | 序列化结果 **>8000** → spill | [TOOLS.md](./TOOLS.md) §6.4 | **工具返回值** 进 LLM 前落盘；与 **写入参数** 无关 |
| **L2b** | spill 预览 **2000** 字 | §6.4 `TOOL_OUTPUT_PREVIEW_CHARS` | 返回给模型的摘要长度 |
| **L3 读盘** | 单文件 **≤512KB** | [TOOLS.md](./TOOLS.md) §7.1 `read_file` | 磁盘上已有文件可读上限 |
| **L4 日志** | 参数日志默认 **500** 字 | `evolve_log` `EVOLVE_LOG_ARG_MAX_CHARS` | 仅审计日志，**不**拦执行 |
| **—** | `write_text` 的 `content` | 现 **无** 上限 | 助手可用它把整份 `main.py` 塞进 `tool_calls` → **历史膨胀**（本次事故绕路） |

#### 9.1.2 T-1511 已决规则

**目的**：inline 载荷（不论 plain 还是 base64 **解码后**）过大时，**不进 confirm**，逼走 staging + `content_workspace_path` — 与 CONFIRM-PIPELINE **C8**（prompt 软约束）同向，改为 **executor 硬约束**。

| 规则 | 阈值 | 适用 |
|------|--------------|------|
| **R8a** | 解码后正文 **`> WRITE_INLINE_MAX_CHARS`（默认 8192）** 且无 `content_workspace_path` | `write_evolve` 全路径；`content_base64` 先 `b64decode` 再量长度 |
| **R8b** | 同上 | `run_evolved` → **`write_text` / `append_text`** 的 `content`（堵 staging 绕路） |
| **豁免** | 有 **`content_workspace_path`** | 正文在 workspace 文件里，tool 参数只有路径 → **允许任意大**（仍受 L3 读盘 512KB 若之后 `read_file`） |
| **不动** | L1 的 **240 / 换行 / 引号** | 仍保留；小文件 plain 写 `main.py` 一行短片段仍可用 |

**为何默认 8192？**

- **8000**（`TOOL_OUTPUT_SPILL_CHARS`）管 **工具返回值** 进 LLM 前的 spill。
- **8192** 管 **写入参数** 内联进 `tool_calls` / `messages.jsonl`；与输出 spill **同量级**，略高一点留 JSON/base64 开销余量。
- 仍可通过 env `WRITE_INLINE_MAX_CHARS` 覆盖；**不必**与 `TOOL_OUTPUT_SPILL_CHARS` 强行绑定同一变量。

**与 240 的关系**：**不冲突、两层都要**。

```text
写 main.py 一段多行代码：
  L1：plain + 含换行 → 已拒（须 b64 或 workspace_path）
  L1：若强行单行无引号且 ≤240 → 仍可通过（极短片段）
  R8a：b64 解码后 >8192 → 拒（须改 content_workspace_path）← 本次 npm_exec 类事故
```

#### 9.1.3 推荐落地（T-1511）

```python
# 伪代码 — executor 统一入口 _inline_body_guard(tool, path, arguments)
INLINE_MAX = int(os.environ.get("WRITE_INLINE_MAX_CHARS", "8192"))
body = plain_or_b64_decoded(arguments)
if len(body) > INLINE_MAX and not has_workspace_path(arguments):
    return validation_error("…请 write_text → workspace/_staging* → content_workspace_path")
```

| 环境变量 | 默认 | 说明 |
|----------|------|------|
| `WRITE_INLINE_MAX_CHARS` | `8192` | T-1511 内联写入上限（**输入**） |
| `TOOL_OUTPUT_SPILL_CHARS` | `8000` | 已有；**输出** spill（不变） |

#### 9.1.4 非目标

- **不**降低 `read_file` 512KB（读已有大文件仍合法）
- **不**用 8192 限制 `content_workspace_path` 指向的文件大小
- **不**属于 Phase 15 M0；已迁入 Phase 16 T-1511

### 9.2 无事件看门狗

`STALL_WATCHDOG_SEC` 默认 `0`（关闭）；显式设为 `180` 时，若 `_turn_busy` 且 180s 无 `assistant.delta` / `tool.start` / `tool.end` / `confirm.request` / `confirm.done`：

- sidecar 主动 `request_cancel()` + `notice`「回合超时无响应，已自动停止」，最终 `finish_reason=timeout`

`reasoning.delta` **不计进度**，避免无限 reasoning 掩盖 stall。慢 pro 模型存在误杀风险，因此默认关闭，由用户显式启用；完整语义见 RUNTIME-GUARDS G5～G7。

### 9.3 `finish_reason=timeout` 分型（BUG-023 · 待 T-2093）

当前桌面凡 `finish_reason=timeout` 均显示 **「回合超时已停止」**，以下来源**未区分**：

| 来源 | sidecar 典型 notice | agent `finish_reason` |
|------|----------------------|------------------------|
| **LLM 请求** | （现状无专用 notice） | `LLMTimeoutError` → `timeout` |
| **回合墙钟** | 「回合已超过墙钟限制，已自动停止」 | watchdog → `timeout` |
| **stall 看门狗** | 「回合超时无响应，已自动停止」 | watchdog → `timeout` |

**已决（T-2093）**：`LLMTimeoutError` 须先发 `turn.notice`（含秒数）；桌面可继续用「回合超时已停止」作 `turn.end` 收口，或细分为「LLM 请求超时」— 以 [bugs/2026-08-05-compact-turn-llm-timeout.md](./bugs/2026-08-05-compact-turn-llm-timeout.md) §6 R4 为准。

**与压缩叠加**：自动压缩后的第一次主 LLM 超时，用户体感为「刚压缩就卡死」— 根因在摘要+主 LLM 串行占满 120s，见 RUNTIME §8.4。

---

## 10. 验证

### 10.1 自动化

```powershell
Set-Location agent-core
python tests/test_turn_cancel.py
python tests/test_runtime_guards.py
python server.py --demo   # 含 turn cancel / confirm timeout 90s 缩略 demo
```

### 10.2 手工清单（grow）

| # | 步骤 | 通过标准 |
|---|------|----------|
| 1 | 发长问题，出现「思考中…」 | Stop 按钮可见 |
| 2 | 点 Stop | ≤3s 状态「已停止」→「就绪」；可发下一条 |
| 3 | 触发 `write_text` confirm，不点，点 Stop | confirm 标「已取消」；不永久「提交中…」 |
| 4 | 设 `CONFIRM_TIMEOUT_SEC=10`，挂 confirm | 10s 后 `timeout` + 可继续 |
| 5 | 托盘退出重开 | 同会话续聊无 `insufficient tool messages` |

### 10.3 回归

- Phase 14：`test_confirm_pipeline.py` 全 PASS  
- BUG-008 路径：错 ID 仍不空转  

---

## 11. 非目标（Phase 15）

- 多窗口 / 多会话并行取消  
- 取消后自动 `git checkout` 回滚 evolve 写入  
- 替换 `write_evolve` 为 IDE 式直接写盘（架构级，另 Phase）  
- 模型自动降档（flash ↔ pro）— 见 RUNTIME / 主题策略另议  

---

## 12. 变更记录

| 版本 | 日期 | 说明 |
|------|------|------|
| 0.1.0 | 2026-07-13 | 初稿：Stop · `turn.cancel` · confirm 90s · LLM 协作取消 |
| 0.2.0 | 2026-07-13 | M0 implemented；确认取消竞争加固；明确同步工具须等 `turn.end` |
