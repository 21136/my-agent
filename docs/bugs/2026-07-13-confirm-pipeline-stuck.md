# BUG-008：工具确认后长时间「执行中」/ 回合线程卡死

- **日期**：2026-07-13
- **发现于**：Electron 桌面壳 **grow**；`run_evolved` → `write_evolve`（`npm_exec` scaffold）
- **状态**：**fixed**
- **设计**：[CONFIRM-PIPELINE.md](../CONFIRM-PIPELINE.md) · Phase 14 T-1302～T-1308

---

## 现象

用户点击工具确认「同意」后，底栏状态显示 **「已提交确认，执行中…」**，可持续 **30 分钟以上**。聊天区无新助手回复，输入框行为因壳而异（可能禁用或看似可发）。

会话 `20260712-c369be88` 的 `messages.jsonl` 在 **10:19:52** 后不再更新；`evolve_log` 显示首轮 `write_evolve` 在 **123ms** 内已失败，并非长时间执行。

助手随后尝试 `write_text` 写入 `workspace/_staging_npm_main.py`（需 **第二次确认**），用户侧未感知回合已结束。

## 根因

### 主因（P0）：`confirm_fn` 错 `request_id` 无限空转

`agent-core/server.py` 中 `WsBridge.confirm_fn`：

```python
while True:
    resp_id, choice = self._confirm_queue.get(timeout=3600)
    if resp_id == request_id:
        emit confirm.done
        return choice
    self._confirm_queue.put((resp_id, choice))  # 错 ID 放回 → 立即再 get → 永不阻塞
```

当队列中仅有 **过期/错误** 的 `(request_id, choice)`（用户对 **上一张** 确认卡重复点击，或 DOM 重绘后按钮复活）时，循环 **CPU 空转**，**永不触发** 3600s 超时，回合线程 **永久阻塞**。

### 次因（P0）：确认超时无 `confirm.done`

`except queue.Empty: return "n"` 路径 **不** `emit confirm.done`，桌面 `confirmPending` 无法清除（见 BUG-008b）。

### 促成因素（P1）

| 因素 | 位置 |
|------|------|
| `write_evolve` base64 padding 非法 → 快速失败 → 触发第二次 confirm | `write_evolve/main.py` · 模型输出 |
| grow `renderChat()` 全量 `innerHTML` 重绘，确认按钮 `disabled` 丢失 | `grow/index.ts` |
| 点同意时 `setStatus("执行中")` 但 `confirmPending=true` 时 `isWorking()` 为 false | `chat-state.ts` |

## 与已修 BUG 的关系

| 已修 | 关系 |
|------|------|
| BUG-002 WS 死锁 | 已 `create_task` + inline `confirm.response`；**本 bug 为队列逻辑新失效模式** |
| BUG-005 残缺 tool | 若本 bug 卡死在中途，仍可能留下残缺 `tool_calls` |
| BUG-006 TURN_LOCK | 本 incident sidecar 存活，**非**全局锁死锁 |
| BUG-007 新会话 | 独立；修复后仍须保证 confirm 路径对称 |

## 修复

见 [CONFIRM-PIPELINE.md](../CONFIRM-PIPELINE.md) 决议 C1–C10（已实施）：

1. **C1** `deliver_confirm` 拒收错 ID + `confirm_fn` 丢弃队列中的 orphan，禁止无限 `put` 回队
2. **C2** 超时：发 `confirm.done`（`choice: timeout`）+ notice，再返回 `"n"`
3. **C3/C4** 桌面：`submitConfirm` 仅当前 `requestId`；新 confirm 作废旧块
4. **C7** base64 确认前校验
5. **C6/C9** `tool.end` 必发；`turn.end` + 空 `assistant.done`

## 验证

```powershell
Set-Location agent-core
python -m pytest tests/test_confirm_pipeline.py -q
```

手工：grow 壳复现「坏 base64 → 第二次 confirm」→ 点新卡 **60s 内** 必有 `tool.end` 或 `turn.end`；点旧卡 **不** 卡线程。

## 预防

- 任何 `confirm_fn` 等待循环 **必须有界**（错 ID 上限 / 丢弃策略）
- **`confirm.done` 与 `confirm_fn` 返回成对**，含超时与异常
- 桌面 confirm 点击 **写 model 状态**，不依赖 DOM `disabled` 防重入
- scaffold 大文件 **禁止** 依赖 `content_base64` 一次过（见 C8）

## 临时规避（用户）

1. **完全退出** 桌面应用（托盘退出）→ 重启 `start-desktop.bat`
2. 确认时只点 **最新一张**「工具确认」卡；若已卡死，勿重复点旧卡
3. 续聊可指示助手：用 `content_workspace_path`，勿用大段 `content_base64`
