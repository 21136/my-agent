# BUG-001：工具失败时 `_tool_result_summary` 把 `ToolError` 当 dict 用

- **日期**：2026-07-11
- **发现于**：Electron 桌面壳（`grow` shell），WebSocket 后端 `server.py`
- **状态**：fixed
- **修复提交**：工作区本地修复（未单独 commit）

---

## 现象

用户在桌面聊天发送：

> 我是软件工程学生，你看看我们还需要什么工具，推荐一下

界面未返回正常回复，而是直接显示错误：

```text
'ToolError' object has no attribute 'get'
```

状态栏显示「错误」。后端终端持续输出 HTTP 200（WebSocket 轮询正常），说明服务在跑，是单轮处理逻辑崩溃。

## 根因

`ToolResult.error` 的类型是 dataclass `ToolError`（见 `tools/schema.py`），字段为 `code` / `message` / `details`，**不是** JSON dict。

工具执行结束后，`ToolExecutor.run` 会发 `tool.end` 事件，其中 `summary` 由 `_tool_result_summary` 生成：

```python
# 修复前（错误）
def _tool_result_summary(result: ToolResult) -> str:
    if not result.ok and result.error:
        return str(result.error.get("message", "failed"))  # ToolError 无 .get()
```

当 Agent 调用的某个 builtin / evolved 工具返回 `ok=false` 时，走到上述分支即抛 `AttributeError`。异常被 `server.py` 的 `_run_line` 捕获后以 `{"type": "error", "message": str(exc)}` 推到前端，用户看到原始异常字符串。

**触发条件**：任意工具调用失败（如校验错误、路径不存在、用户拒绝 confirm、API key 缺失等），不限于「推荐工具」这一条用户消息。

## 修复

`agent-core/tools/executor.py`：

```python
def _tool_result_summary(result: ToolResult) -> str:
    if not result.ok and result.error:
        return result.error.message or "failed"
```

与同文件及其他模块一致：`tools/logging.py`、`web_search.py` 等均使用 `result.error.message` / `result.error.code`。

## 验证

```powershell
Set-Location agent-core
python -c "from tools.schema import ToolErrorCode, tool_fail; from tools.executor import _tool_result_summary; r = tool_fail('grep', ToolErrorCode.VALIDATION_ERROR, 'pattern is required'); print(_tool_result_summary(r))"
# 输出: pattern is required

python tools/executor.py
# 全部 [PASS]
```

修复后需**重启桌面应用或 Python 后端**以加载新代码。

## 预防

- `ToolError` 与 `ToolResult` 是 dataclass；序列化用 `.to_dict()`，读取用属性访问。
- 若后续为 `tool.end` summary 加单测，应覆盖 `ok=false` 分支，避免只测成功路径。
- 代码审查时注意 `error.get(` / `result.error.get(` 类写法——多为从 JSON dict 时代遗留。
