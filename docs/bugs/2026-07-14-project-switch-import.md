# BUG-019：项目切换 `session_memory_event` 错从 `session` 导入

- **日期**：2026-07-14
- **发现于**：桌面 project 壳；侧栏点击切换项目（`project.switch`，`session_replaced=true`）
- **状态**：**fixed**

---

## 现象

聊天区输入条上方蓝条显示：

```text
cannot import name 'session_memory_event' from 'session' (D:\my-agent\agent-core\session.py)
```

切换项目失败或切换后聊天区未灌入 `session.history` / `session.memory`。

## 根因

`project_api.perform_project_switch` 在会话替换分支误写：

```python
from session import session_history_event, session_memory_event
```

- `session_history_event` 定义在 **`session.py`**
- `session_memory_event` 定义在 **`context.py`**（与 `server.py` · `agent.py` 一致）

`session_replaced=True` 时才执行该懒导入，故仅在**跨项目切换并替换专用会话**时触发。

## 修复

`project_api.py` 拆分为：

```python
from context import session_memory_event
from session import session_history_event
```

## 验证

```powershell
Set-Location agent-core
python -c "from project_api import perform_project_switch; print('ok')"
```

桌面：project 壳侧栏切换至另一已绑定项目 → 应出现 `project.switch.done`，聊天区由 `session.history` 替换，无 ImportError 蓝条。

**改代码后**：完全退出托盘/Electron → 重新 `start-desktop.bat`。
