# Luna 网关对照（0x567 vs Tokeness）

> 版本 **0.1.1** · 2026-10-15  
> 关联：[REASONING-EFFORT.md](./REASONING-EFFORT.md) · [MAP.md](./MAP.md) §8 · `agent-core/llm_client.py` · `tools/llm_luna_gateway_probe.py`

---

## 0. 摘要

my-agent 当前内置的 OpenAI-compatible 网关默认模型如下：

| 注册 ID | 网关 | 请求模型名 | 上下文 | Base URL |
|---------|------|------------|--------|----------|
| `0x567-flash` | 0x567 | `gpt-6-luna` | 1M | `https://api-cdn.0x567.com` |
| `0x567-pro` | 0x567 | `gpt-6-sol` | 1M | `https://api-cdn.0x567.com` |
| `tokeness-luna` | Tokeness | `gpt-5.6-luna` | 372k | `https://n.tokeness.dev` |

以上是本地内置配置的默认请求名；下方 2026-09-03 的 0x567 live 记录是旧模型实测，不能证明当前 GPT-6 上游路由已通过真实 API 验证。

agent 主循环几乎每轮都带 `tools` + 会话 `reasoning_effort`（默认 `medium`）。

| 上游模型 | 带 `tools` 时客户端行为 |
|----------|-------------------------|
| **GPT-6 Luna / Sol**（0x567） | **省略** `reasoning_effort` 字段（2026-10-15 实测：传 `none` 会 400） |
| **Tokeness gpt-5.6-luna** | 强制 `reasoning_effort: "none"`（否则 400） |

实现：`llm_client._gpt6_omit_reasoning_effort_with_tools()` · `is_tokeness_gateway()` 分支。

---

## 1. 求证方法

```powershell
.venv\Scripts\python.exe tools\llm_luna_gateway_probe.py --provider 0x567
.venv\Scripts\python.exe tools\llm_luna_gateway_probe.py --provider tokeness
```

密钥：`OX567_API_KEY` / `LLM_tokeness_KEY`（环境变量或 `data/llm_secrets.json`）。

探测项：

1. **同步**：`tools+medium` · `tools+none` · `tools_only` · `no_tools+medium`
2. **流式**：`tools+medium` · `tools+none` · `tools_only`（统计 `reasoning_len` · `tool_call_deltas`）

---

## 2. 历史实测：0x567-flash · GPT-5.6 Luna（2026-09-03）

环境：本机 `data/llm_secrets.json` · `gpt-5.6-luna` · `https://api-cdn.0x567.com/v1/chat/completions`

> 此结果验证的是 2026-09-03 使用的 GPT-5.6 Luna 请求，不是当前 GPT-6 Luna / GPT-6 Sol 默认模型的实测。

### 2.1 同步请求

| Case | HTTP | 说明 |
|------|------|------|
| `tools` + `reasoning_effort: medium` | **200** | 与 agent 主循环一致 |
| `tools` + `reasoning_effort: none` | **200** | |
| `tools` only | **200** | |
| 无 `tools` + `medium` | **200** | |

### 2.2 流式请求

| Case | HTTP | reasoning_len | tool_call_deltas |
|------|------|---------------|------------------|
| `tools` + `medium` | 200 | 35 | 2 |
| `tools` + `none` | 200 | 0 | 0 |
| `tools` only | 200 | 42 | 2 |

**结论**：

- 0x567 **不会**因 `tools + reasoning_effort` 返回 400。
- `medium` 与 `none` 行为可区分；`none` 时无 reasoning 流。
- 即使不传 `reasoning_effort`，仍可能收到 reasoning 流（网关/模型默认行为）。

与 [RUNAWAY-EXPERIENCE.md](./archive/RUNAWAY-EXPERIENCE.md)（S-6011 · `0x567-flash` 狂奔可跑通）一致。

---

## 3. Tokeness · 结果

### 3.1 控制台实证（2026-09-03）

用户 Tokeness 使用日志（请求模型 `gpt-5.6-luna`）：

```text
status_code=400
Function tools with reasoning_effort are not supported for gpt-5.6-luna
in /v1/chat/completions.
To use function tools, use /v1/responses or set reasoning_effort to 'none'.
```

UI 层可能先显示 `temporarily_unavailable` / 渠道类文案；**以控制台 400 正文为准**。

### 3.2 my-agent 修复

| 条件 | 发往 API 的 `reasoning_effort` |
|------|-------------------------------|
| Tokeness · 无 tools | 会话档位（`medium` / `high` 等） |
| Tokeness · 有 tools | **`none`**（强制） |
| 0x567 · 有/无 tools | 会话档位（不变） |

### 3.3 本地复现 Tokeness

在 shell 中配置 `LLM_tokeness_KEY` 后运行：

```powershell
.venv\Scripts\python.exe tools\llm_luna_gateway_probe.py --provider tokeness
```

预期（修复前 agent 行为）：`sync_tools+reasoning_medium` → **400**；`sync_tools+reasoning_none` → **200**。

---

## 4. 对产品的含义

| 问题 | 答案 |
|------|------|
| 0x567 是否也有 Tokeness 同款 400？ | **否**（live 求证 200） |
| 以前 0x567 狂奔失败是参数问题吗？ | **主要不是**；文档记录为 503/504/pool/超时 |
| 切 Tokeness 要注意什么？ | 工具循环下 **无法** 同时开 `reasoning_effort`（chat/completions） |
| 能否在 Tokeness 上恢复 effort+tools？ | 需接 `/v1/responses`（未实施） |

---

## 5. 配置速查

```powershell
# 0x567 Luna
$env:OX567_API_KEY = "sk-..."
$env:LLM_MODEL = "0x567-flash"

# Tokeness Luna
$env:LLM_tokeness_KEY = "sk-..."
$env:LLM_MODEL = "tokeness-luna"
# 可选覆盖模型广场全名
$env:TOKENESS_MODEL_LUNA = "gpt-5.6-luna"
```

---

## 6. 变更记录

| 日期 | 变更 |
|------|------|
| 2026-09-03 | 初稿：0x567 live 求证 + Tokeness 400 对照 + `llm_luna_gateway_probe.py` |
