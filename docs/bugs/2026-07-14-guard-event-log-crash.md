# BUG-017：guard 拦截后记日志崩溃（`guard_type` 重复传参）

- **日期**：2026-07-14
- **发现于**：桌面壳 grow；`run_evolved` → `write_text`；用户按 `a` 批准 workspace 免确认后
- **状态**：**fixed**
- **关联**：[RUNTIME-GUARDS.md](../RUNTIME-GUARDS.md) §G11 · Phase 16 T-1511 · **非** Phase 17 checker 职责

---

## 现象

UI / 日志先出现正常事件：

```text
session_workspace_approved: {"tool_name": "write_text"}
```

随后工具回合报错：

```text
tools.logging.EvolveLog.log_guard_event() got multiple values for keyword argument 'guard_type'
```

用户可能误以为「监工没管」或「批准 `write_text` 坏了」；实际是 **内联写入超限 guard 记日志时崩溃**。

## 根因

Phase 16 `inline_write_max` 校验失败时，`ToolResult.error.details` 已含 `guard_type`：

```python
details={
    "guard_type": "inline_write_max",
    "decoded_chars": longest,
    "limit": limit,
    "tool_name": evolved_name,
}
```

`ToolExecutor._record_guard_event()` 将 `details` 展开为 `**fields`，同时又显式传入 `guard_type=guard_type`：

```python
self.evolve_log.log_guard_event(
    guard_type=guard_type,
    conversation_id=...,
    **fields,  # fields 内仍有 guard_type → TypeError
)
```

**约束本身已生效**（`validate` 返回失败）；崩溃发生在写 `evolve_log.jsonl` 的 `event: guard` 时。

### 与监工（checker）的分工

| 层 | 职责 | 本 incident |
|----|------|-------------|
| Phase 16 guard | 运行时执法（内联上限等） | **应拦截**超大 `content` |
| Phase 17 checker | scaffold 落盘后的 PASS/FAIL 审计 | **不介入**日常 `write_text` |

`session_workspace_approved`（按 `a`）只免 **confirm**，不免 `inline_write_max`。

## 修复

`agent-core/tools/executor.py` — `_record_guard_event` 在调用 `log_guard_event` 前：

```python
fields.pop("guard_type", None)
```

## 验证

```powershell
Set-Location agent-core
python -m unittest tests.test_runtime_guards_m1.InlineWriteGuardTests.test_inline_write_guard_event_logs_without_crash -q
```

预期：`run()` 拒绝 9000 字符内联 `write_text`，`evolve_log` 写入一条 `event: guard` 且 `guard_type=inline_write_max`，无 TypeError。

## 预防

- 显式关键字参数与 `**kwargs` 展开并存时，从 payload 中剔除同名键。
- guard 相关单测除 `validate` 外，应覆盖 **`executor.run()` → evolve_log** 全路径（T-1511 已有 validate 断言，补 logging 回归）。

## 用户规避

大文件写入走：`write_text` → `workspace/_staging*` → `content_workspace_path`，勿内联超大 `content`。
