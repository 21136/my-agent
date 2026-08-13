# 推理强度控制（REASONING-EFFORT）

> 版本 **0.3.0** · 2026-08-13  
> 对标 Claude Code `/effort`；实现见 `llm_client._api_reasoning_effort` · `session.normalize_reasoning_effort`。  
> Terminal auto-plan 与 effort 的关系见 [TERMINAL-MODE.md](./TERMINAL-MODE.md) §5.5.5。

---

## 1. 动机

用户可在会话中调节「这轮多花 token 思考」或「快速回答」。除模型档位（flash / pro）外，`SessionMeta.reasoning_effort` 控制**主循环**与部分子调用发往 API 的推理强度。

---

## 2. 会话等级（用户可见）

`session.py` 合法值：`low` · `medium` · `high` · `max`（默认 `medium`）。

| 等级 | REPL | 含义 |
|------|------|------|
| `low` | `effort low` / `推理强度 low` | 省 token、偏速度 |
| `medium` | `effort medium` | **默认**，平衡 |
| `high` | `effort high` | 更深推理 |
| `max` | `effort max` | 最高推理（Terminal plan 逻辑档位） |

非法值 → `medium`。环境变量 `LLM_REASONING_EFFORT` 可设新 session 默认。

---

## 3. 厂商 API 映射（实现真相）

**用户/会话存的是「逻辑档位」；`llm_client.chat(reasoning_effort=…)` 在发请求前按厂商映射为 API 可接受的值。**

实现：`agent-core/llm_client.py` → `_api_reasoning_effort()` · `_apply_reasoning_effort_to_payload()`。

### 3.1 映射表（session → API 字段值）

| 会话 `reasoning_effort` | DeepSeek / Sophnet | `0x567` | 其他厂商 |
|-------------------------|-------------------|---------|----------|
| `low` | `low` | `low` | `low` |
| `medium` | **`high`** | `medium` | `medium` |
| `high` | `high` | `high` | `high` |
| `max` | `max` | **`high`** | `max` |

说明：

- **DeepSeek / Sophnet**：API 使用 `thinking` 对象，不认平铺 `medium`；`medium` 映射为 `high`。见 §3.2。
- **0x567**（如 gpt-5.4 网关）：使用顶层 `reasoning_effort`；**不支持 `max`**（会 `invalid parameter`），故 `max` → `high`。TUI 仍可显示逻辑标签 `max`。
- **其他厂商**：原样透传（OpenAI 兼容网关按各自模型文档）。

### 3.2 请求体形态（按厂商）

| 厂商 | payload 字段 | 示例 |
|------|--------------|------|
| `DeepSeek` · `Sophnet` | `thinking.type` + `thinking.reasoning_effort` | `{"thinking": {"type": "enabled", "reasoning_effort": "high"}}` |
| `0x567` | 顶层 `reasoning_effort` | `{"reasoning_effort": "high"}` |
| 其他 | 顶层 `reasoning_effort`（若传入） | 与 OpenAI 兼容 |

**不要**对 DeepSeek 发平铺 `reasoning_effort`；**不要**对 0x567 发 `thinking` 对象（由 `_apply_reasoning_effort_to_payload` 分支处理）。

### 3.3 DeepSeek / Sophnet 模型侧能力（API 接受值）

`thinking.reasoning_effort` 常见为 `low` · `high` · `max`（以提供商文档为准）。

| API 值 | flash | pro |
|--------|-------|-----|
| `low` | ✅ | 常降级为 `high` |
| `high` | ✅ | ✅ |
| `max` | ✅ | ✅ |

### 3.4 UI 标签 vs API 实发

| 场景 | TUI / notice 显示 | 实际 API |
|------|-------------------|----------|
| 会话 `medium` + DeepSeek flash | `medium` | `high` |
| Terminal plan + `0x567-pro` | `max`（逻辑档位） | `high` |
| 弱 API 降级规划 | `high · 降级` | `high` |

原则：**展示逻辑档位**（用户选的或 plan  profile 定的），**发送映射后的值**；避免网关拒参。

---

## 4. 作用范围

| 调用方 | `reasoning_effort` | 说明 |
|--------|-------------------|------|
| 主循环 `agent.py` S4 | **会话值** | 每轮 `chat(..., reasoning_effort=meta.reasoning_effort)` |
| Terminal plan / replan 子代理 | **profile**：pro 时 `max`，降级时 `high` | `resolve_terminal_planning_profile()` |
| Terminal plan 入口分类器 | **不传** | `topic_routing` flash，`temperature=0` |
| explore / checker / plan_agent 等 | **不传** | 固定 temperature，确定性后台调用 |
| 主题路由 S2 | **不传** | `temperature=0` |

---

## 5. Terminal auto-plan 与 effort

见 [TERMINAL-MODE.md](./TERMINAL-MODE.md) §5.5.5。

- **规划模型**：`resolve_terminal_planning_profile()` — 同厂商有 key 的 pro → 该 pro + 逻辑 `max`；否则 `main_turn` + 逻辑 `high`（`degraded`）。
- **执行模型**：`main_turn` + 会话 `reasoning_effort`。
- **API 裁剪**：规划即使用逻辑 `max`，经 §3.1 映射后 `0x567` 仍发 `high`。

---

## 6. API 层（`llm_client.chat`）

```python
response = llm.chat(
    messages,
    model=model,
    reasoning_effort=self.session.meta.reasoning_effort,  # 逻辑档位
    stream=handlers,
)
```

内部：

```python
if reasoning_effort is not None:
    _apply_reasoning_effort_to_payload(payload, reasoning_effort, entry.vendor)
```

校验：`normalize_reasoning_effort()` / `_normalize_reasoning_effort()` → 非法则 `medium`。

---

## 7. REPL 命令

```
effort <level>          # effort low | medium | high | max
推理强度 <level>         # 推理强度 high
```

`main.py` · `cli_terminal.py` 路由至 `session.meta.reasoning_effort` 并 `save()`。

---

## 8. 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `LLM_REASONING_EFFORT` | — | 新 session 默认 effort（经 normalize） |

---

## 9. 测试

| ID | 文件 | 覆盖 |
|----|------|------|
| IT | `tests/test_reasoning_effort.py` | DeepSeek `medium→high`、0x567 顶层字段、`max→high` |
| IT-606 | `tests/test_terminal_plan_execute.py` | plan profile 降级 / pro |

---

## 10. 开放问题

| # | 问题 | 状态 |
|---|------|------|
| 1 | pro 上 DeepSeek `low` API 降级是否 CLI 提示 | 可选 |
| 2 | `max` 超时是否延长 `LLM_TIMEOUT_SEC` | 待实测 |
| 3 | 是否在 system prompt 写明当前 effort | 暂不 |

---

## 附录 · 历史（0.2.0 设计稿）

初稿仅覆盖 DeepSeek `thinking` 三档（`low`/`high`/`max`）。0.3.0 补齐：**四档会话值**、**多厂商 payload**、**0x567 max 裁剪**、**Terminal plan profile**。
