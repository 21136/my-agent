# BUG-020：activity_router × park_session 污染 shell_sessions（STD-001）

- **日期**：2026-07-18
- **发现于**：稳定化 P0 S-14 / S-16；放行后债 [STD-001](../stabilization-backlog.md#std-001-activity_router--park_session-污染-shell_sessions)
- **严重度**：P1
- **根因类**：B 异步接缝 + C 生命周期（[STABILIZATION.md](../STABILIZATION.md) §1）
- **覆盖面**：S-14 · S-16 · `activity_router.py` · `shell_switch.park_session` · `agent.py`
- **状态**：**fixed**

## 现象（最少复现步骤）

1. `shell_sessions` 已分离：`grow→G` · `daily→D`
2. 在 **grow** 会话发纯 Q&A（如 `2+2`，无 `proposal` 等 grow 词）
3. `compute_activity_route` 对 `intent=qa` 返回 `shell=daily`；`agent` 把 `meta.active_shell` 写成 `daily`
4. 随后 `shell.switch` → daily 时 `park_session` 按 `active_shell=daily` 写入 → **`shell_sessions.daily = G`**
5. 伴侶 / daily 线加载错误会话，history 串线

最小单测复现（修前）：grow 会话手动 `active_shell=daily` → `park_session` → `daily` 映射变成 grow id。

## 根因

1. **`activity_router`**：`qa` / `recall` / `plan` 默认路由到 `daily`（UI 建议），合理。
2. **`agent.py`**：把路由结果**持久化**进 `session.meta.active_shell`，把「UI 建议」当成「会话线归属」。
3. **`park_session`**：按 `meta.active_shell` 写 `shell_sessions`，无反查归属 → 污染另一壳线的映射。

## 修复

1. **`park_session`**：优先按 `shell_sessions` **反查**当前 `conversation_id` 的归属壳线再 park；并回写纠正漂移的 `meta.active_shell`。
2. **`switch_shell`**：比较目标壳与 **归属线**（非仅 `meta.active_shell`），避免 meta 已标成 daily 时误判「已在 daily」而空转。
3. **`activity_router.should_persist_activity_shell`** + **`agent.py`**：grow↔daily 的软路由（如「对话 / 方案」）**不**改写持久化 `active_shell`；仍 emit `ui.route` 供桌面参考。project / govern / 升到 grow 的硬路由仍持久化。

## 验证

```powershell
Set-Location agent-core
python -m unittest tests.test_shell_session_ownership -v
python tests/run_stabilization.py
```

期望：`park` 在 meta 被标成 daily 时仍保持 `grow≠daily` 映射；grow 上 QA 后 `active_shell` 仍为 grow。

**改代码后**：完全退出托盘/Electron → 重新 `start-desktop.bat`。
