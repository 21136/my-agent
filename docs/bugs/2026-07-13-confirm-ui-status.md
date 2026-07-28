# BUG-009～013：确认管线 UI / 执行状态

- **日期**：2026-07-13
- **发现于**：桌面壳 grow / project（daily / pet 部分同源）
- **状态**：**fixed**（与 [BUG-008](./2026-07-13-confirm-pipeline-stuck.md) 同批加固）
- **设计**：[CONFIRM-PIPELINE.md](../CONFIRM-PIPELINE.md)

---

## BUG-009：旧确认卡可重复点击（P1）

### 现象

用户点「同意」后按钮短暂 disabled；`renderChat()` 重绘后 **旧卡按钮恢复可点**。再次点击发送 **过期 `request_id`**，诱发 BUG-008 空转。

### 根因

`desktop/src/shells/grow/index.ts`：`renderBlock` 仅当 `block.resolved` 时 disabled；点击 handler **未** 乐观写入 `resolved`。`project/index.ts` 同类，且无 `block.resolved` 守卫。

### 修复方向

C3/C4：点击即 `resolved`；`requestId !== confirmOverlay.requestId` 忽略；新 `confirm.request` 将旧块标「已过期」。

---

## BUG-010：状态栏「执行中」与真实 busy 不一致（P1）

### 现象

底栏「已提交确认，执行中…」，但全窗 `is-working` / `setAgentBusy` 可能 **未亮**；或 `confirm.done` 后立刻「就绪」，工具尚未 `tool.start`。

### 根因

- `chat-state.ts`：`isWorking()` 在 `confirmPending` 时为 **false**
- `grow/index.ts`：`confirm.done` → 硬编码 `setStatus("就绪")`，覆盖 `tool.start` 前真实状态

### 修复方向

C5：统一 `onConfirmRequest` / `onConfirmDone` / `onToolEnd` / `assistant.done` 驱动 status；引入 `confirmSubmitting` 或等价标志。

---

## BUG-011：`tool.end` 缺失导致永久 working（P1）

### 现象

异常后壳面 busy 不消，`toolsRunning` 不归零。

### 根因

`executor.py` `ToolExecutor.run`：`tool.start` 与 `tool.end` 之间无 `try/finally`；`_execute_builtin` 等抛错时跳过 `tool.end`。

### 修复方向

C6：`finally` 中发 `tool.end`（`ok: false`）。

---

## BUG-012：空 assistant 无 `assistant.done`（P1）

### 现象

回合在服务端结束，桌面 `turnActive` 一直 true，「处理中…」。

### 根因

`main.py` 仅当 `result.assistant_text` 非空时 `assistant_output_fn`；无对称结束事件。

### 修复方向

C9：`turn.end` 或空文本 `assistant.done`；`_run_line` finally 保证发送。

---

## BUG-013：`content_base64` 确认后才失败（P1）

### 现象

用户确认 `write_evolve` 后 **123ms** 报 padding 错误；助手改 staging → **又一次** 确认，放大 BUG-008 概率。

### 根因

`executor.py` `_write_evolve_content_guard` 只检查字段存在；`write_evolve/main.py` 在执行阶段才 `b64decode(validate=True)`。

### 修复方向

C7 确认前解码；C8 prompt 优先 `content_workspace_path`。

---

## 验证（修复后）

- 单测 + `server.py --demo` 扩展 PASS 项
- grow 手工：双 confirm 卡场景 + 状态栏与 busy 一致
- `write_evolve` 故意坏 b64：**不出现**确认卡（直接 validation 失败给模型）
