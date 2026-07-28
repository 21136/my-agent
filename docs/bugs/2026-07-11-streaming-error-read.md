# BUG-004：流式 LLM 报错时显示 httpx `read()` 异常

- **日期**：2026-07-11
- **发现于**：Electron `grow` shell，桌面流式对话
- **状态**：fixed

---

## 现象

用户提问后，聊天区显示：

```text
Attempted to access streaming response content, without having called 'read()'.
```

状态栏「错误」。终端仍可能有大量 Vite `200` 日志（无关）。

## 根因

桌面壳为流式输出设置了 `StreamHandlers`，`LLMClient.chat(..., stream=handlers)` 走 `_chat_stream()` → `client.stream("POST", …)`。

当 API 返回 **HTTP ≥ 400**（如 key 无效、余额不足、限流）时，代码调用 `_extract_http_error(response)`，其中直接使用 `response.json()` / `response.text`。

对 **httpx 流式 Response**，必须先 `response.read()` 才能读 body；否则 httpx 抛出上述异常，**掩盖真实 API 错误信息**。

## 修复

`agent-core/llm_client.py`：

- 新增 `_response_text()`：流式用 `read()`，非流式用 `.text`
- `_extract_http_error()` 统一经 `_response_text()` 解析 JSON

## 验证

```powershell
Set-Location agent-core
python llm_client.py
# 应看到 [PASS] _extract_http_error: streaming error body
```

修复后重启桌面应用；若 API 仍有问题，界面应显示 **真实错误**（如 `invalid api key`），而非 httpx 内部异常。

## 预防

凡 `client.stream()` 得到的 `Response`，错误处理路径禁止直接访问 `.text` / `.json()`。
