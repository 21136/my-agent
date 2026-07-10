# 工具系统设计（TOOLS）

> 版本 0.2.3 · 2026-07-09 · 与 `MEMORY.md`（`evolve/_index.toml`）配套  
> M1a 设计文档，**先评审再写代码**

---

## 1. 目标

定义 my-agent 的 **唯一执行面**：LLM 只能通过 **已注册的 tool** 读信息或改变文件系统，不得假装执行。

设计约束（已决）：

| 约束 | 说明 |
|------|------|
| **Builtin 封顶 6 个** | 长期不随使用增长 |
| **Evolved 按主题目录放置** | `evolve/tools/<topic>/` + `tools/common/` |
| **执行唯一口** | 所有 evolved 仅经 `run_evolved` 调用 |
| **主题过滤清单** | 会话确认主题后，仅向 LLM 列出相关 evolved + **全部 common** |

本阶段 **不涉及** skill 自动路由、proposal 自动生成脚本。

---

## 2. 设计原则

| 原则 | 说明 |
|------|------|
| **显式注册** | 未在 registry 里的动作不存在 |
| **可审计** | 每次调用记 `evolve_log` |
| **可确认** | 破坏性/执行类默认 `confirm: true`，不可全局关闭 |
| **可 dry-run** | evolved 支持 `dry_run: true` |
| **路径相对 agent 根** | 禁止写死盘符 |
| **结果结构化** | 统一 JSON 返回 |
| **主题与记忆共用索引** | `evolve/_index.toml` 一处维护 |

---

## 3. Builtin（固定 6 个，不分主题）

**始终**暴露给 LLM；任何 session 都可用。不按主题过滤。

| name | 作用 | confirm | dry-run |
|------|------|---------|---------|
| `read_file` | 读文本文件（有大小上限） | 否 | 否 |
| `list_dir` | 列目录（可递归一层） | 否 | 否 |
| `grep` | 在路径下搜**本地**文件内容 | 否 | 否 |
| `web_search` | **上网**搜索（query → 标题/链接/摘要） | 否 | 否 |
| `fetch_url` | 拉取指定 URL 正文（文本/markdown） | 否 | 否 |
| `run_evolved` | 调用 `evolve/tools/` 已注册脚本 | **是** | 透传 |

Builtin 代码在 `agent-core/tools/builtin/`；**不**放在 `evolve/tools/`。

### 3.1 观察 vs 执行

```text
本地观察：read_file · list_dir · grep
网络观察：web_search · fetch_url
动手执行：run_evolved → evolve/tools/*
```

`grep` ≠ `web_search`：前者搜磁盘，后者搜互联网；**不可合并**。

### 3.2 网络 Builtin 配置

- API Key 仅存 **本机环境变量**，不进 tool 参数、不进 U 盘
- `web_search` 默认复用 **`LLM_API_KEY`**（DeepSeek 原生搜索）；可选 `provider=brave` 时用 `BRAVE_SEARCH_API_KEY`
- `fetch_url` **无 API key**；`httpx` GET；SSRF 拦私网；见 §7.5
- 结果条数/长度上限，与 `grep` 同样防 context 爆炸
- 无 key / 无网 / 配额 / 超时 → 结构化 `error`，禁止 LLM 编造搜索结果

---

## 4. Evolved（按主题 + common）

### 4.1 目录布局（已决）

```text
evolve/
├── _index.toml              # 主题：prompt + memory + tool_dirs
├── tools/
│   ├── common/              # 跨主题；每个 session 都列入 LLM 清单
│   │   └── write_text/
│   │       ├── tool.toml
│   │       └── main.py
│   ├── coding/
│   │   └── <tool_name>/
│   └── workflow/
│       └── sort_downloads/
```

| 路径 | 含义 |
|------|------|
| `tools/common/<name>/` | 跨主题必备（如 `write_text`）；**每 session 都注入清单** |
| `tools/<topic>/<name>/` | 主题专用；仅 session 确认含该 topic 时注入清单 |

由你在放入前 **审阅源码**；内核不自动生成。`status: active` 才可通过 `run_evolved` 被会话清单引用（CLI 调试可跑任意 status，见 §7）。

### 4.2 与 `evolve/_index.toml` 的关系

索引由 [MEMORY.md](./MEMORY.md) 定义；每个 topic 增加 `tool_dirs`：

```toml
[[topic]]
id = "coding"
prompt = "prompts/coding.md"
memory_dirs = ["memories/coding"]
tool_dirs = ["tools/coding"]
```

`tools/common/` **不**写在 topic 里；加载规则写死：**永远并入本会话 evolved 清单**。

### 4.3 会话内 LLM 可见的 evolved 清单

Builtin 恒为 6 个 function；evolved **不**平铺为独立 function，仅在 system 中列目录，经 `run_evolved` 调用：

```text
[本会话可用 evolved 工具]（调用 run_evolved.tool_name）
## common（始终）
- write_text: 向 workspace 写文本文件

## coding（本会话主题）
- format_py: …

## workflow（本会话主题）
- sort_downloads: …
```

主题确认流程见 MEMORY §4；CLI `tool run` **不受**主题限制。

---

## 5. `tool.toml` 清单格式（Evolved）

```toml
[tool]
name = "write_text"
description = "向 workspace 写文本；path 相对 workspace；冲突默认 skip"
version = "1.0.0"
status = "active"          # draft | staged | active | suspect | archived
topics = ["common"]        # 与目录一致；common 工具填 ["common"]

[entry]
type = "python"
path = "main.py"

[schema.input]
type = "object"
required = ["path", "content"]
[schema.input.properties.path]
type = "string"
[schema.input.properties.content]
type = "string"
[schema.input.properties.on_conflict]
type = "string"
enum = ["skip", "rename", "overwrite"]
default = "skip"

[schema.output]
type = "object"
[schema.output.properties]
written = { type = "string" }
skipped = { type = "boolean" }

[policy]
confirm = true
dry_run_supported = true
workspace_only = true
timeout_sec = 60
```

内核扫描 `evolve/tools/**/tool.toml`（含 `common/` 与各 topic 子目录）。

---

## 6. 调用流程

```text
用户输入
    → LLM 选 Builtin（6 个之一）或 run_evolved
    → ToolExecutor.validate（schema、路径、策略）
    → run_evolved：tool_name 须在「本会话清单」或 CLI 显式指定
    → 若 confirm：预览，等待 y/n/a（§6.3）
    → 若 dry_run：透传 evolved
    → 执行 → evolve_log → 回填 LLM（§6.4 超长结果落盘）
```

### 6.3 Confirm 交互（已决）

| 输入 | 行为 |
|------|------|
| `y` | 执行本次 |
| `n` | 拒绝 |
| `a` | **仅** `workspace_only=true` 的 **evolved** tool：本会话后续同类调用 **免 confirm**；写 log `session_workspace_approved`；记入 `meta.json` `workspace_evolved_approved: true` |

Builtin **无** `a`；`workspace_only=false` 的 evolved **无** `a`。

### 6.4 超长 tool 结果落盘（已决，对齐 Cursor）

单次 tool 结构化结果（序列化后）**> 8000 字符**时：

```text
1. 全文写入 data/sessions/<id>/tool_outputs/<uuid>.txt
2. 返回 LLM：前 2000 字符 + output_path + truncated: true
3. LLM 可 read_file / grep 续读
```

适用于 **Builtin 与 evolved**；阈值 env：`TOOL_OUTPUT_SPILL_CHARS`（默认 8000）、`TOOL_OUTPUT_PREVIEW_CHARS`（默认 2000）。

### 6.5 `run_evolved` 参数

```json
{
  "tool_name": "write_text",
  "arguments": { "path": "out.txt", "content": "hello" },
  "dry_run": false
}
```

### 6.6 统一返回格式

```json
{
  "ok": true,
  "tool": "grep",
  "data": { "matches": [{ "path": "...", "line": 1, "text": "..." }] },
  "truncated": false,
  "error": null,
  "duration_ms": 12
}
```

---

## 7. Builtin 详细约定

### 7.1 `read_file`

| 项 | 值 |
|----|-----|
| 参数 | `path`（相对 agent 根或 workspace） |
| 限制 | 单文件 ≤ 512KB；二进制拒绝 |
| 越界 | 必须在 agent 根下 |

### 7.2 `list_dir`

| 项 | 值 |
|----|-----|
| 参数 | `path`, `recursive`（默认 false，true 时仅子一级） |
| 返回 | `{ entries: [{ name, type, size? }] }` |

### 7.3 `grep`（本地）

| 项 | 值 |
|----|-----|
| 参数 | `pattern`, `path`, `glob?`, `ignore_case?`, `max_results?`（默认 50） |
| 实现 | 优先 `rg`；无则 Python 回退 |
| 返回 | `{ matches: [{ path, line, text }], truncated }` |

### 7.4 `web_search`

| 项 | 值 |
|----|-----|
| 参数 | `query`, `max_results?`（默认 5，硬 cap **10**） |
| 返回 | `{ results: [{ title, url, snippet }] }`（外层仍走 §6.2 统一格式） |

**后端（已决）**：默认 **DeepSeek 原生搜索**；可选 **Brave Search API** 后备。

DeepSeek 搜索**不是**独立 REST，而是经 Anthropic 兼容端点 `https://api.deepseek.com/anthropic` 的 **server-side tool**（`web_search_20250305`）。`web_search.py` 在 Builtin 执行器内发起一次 Messages 子调用（`tool_choice` 强制搜索），解析 `web_search_tool_result` 映射为 §7.4；**不**改主循环的 OpenAI 兼容 `llm_client`。实现用 **raw HTTP**，不引入 `anthropic` SDK。

| 环境变量 | 默认 | 说明 |
|----------|------|------|
| `WEB_SEARCH_PROVIDER` | `deepseek` | `deepseek` \| `brave` |
| `LLM_API_KEY` | — | `deepseek` 模式复用（与 [RUNTIME.md](./RUNTIME.md) §6.1 一致） |
| `WEB_SEARCH_MODEL` | `deepseek-v4-flash` | 仅搜索子调用；与主对话 `LLM_MODEL` 可不同 |
| `WEB_SEARCH_ANTHROPIC_BASE_URL` | `https://api.deepseek.com/anthropic` | 一般不改 |
| `WEB_SEARCH_TIMEOUT_SEC` | `15` | 超时秒数 |
| `BRAVE_SEARCH_API_KEY` | — | 仅 `provider=brave` 时必填 |

| 行为 | 说明 |
|------|------|
| 无 key | `deepseek` 且无 `LLM_API_KEY`（或 `brave` 且无 Brave key）→ `ok: false`，`error.code: missing_api_key` |
| snippet | 自 `cited_text` 或留空（`encrypted_content` 不可读）；需正文时 LLM 再调 `fetch_url` |
| 费用 | DeepSeek 按 **token** 计费（含搜索摘要）；Brave 按 Brave 价目 |
| `data.provider` | 可选调试字段（`deepseek` \| `brave`），非 LLM 必填 |

`provider=brave` 时：常规 REST 调 Brave Web Search API，直接填充 `title` / `url` / `snippet`。

### 7.5 `fetch_url`

| 项 | 值 |
|----|-----|
| 参数 | `url`, `max_chars?`（默认 **32000**，硬 cap **128000**） |
| 返回 | `{ url, final_url, content, content_type }`（外层仍走 §6.2 统一格式） |

**后端（已决）**：`httpx` GET；**无 API key**；与 `web_search` 配对（搜索拿链接，本工具拉正文）。

| 环境变量 | 默认 | 说明 |
|----------|------|------|
| `FETCH_URL_TIMEOUT_SEC` | `15` | 超时秒数 |
| `FETCH_URL_MAX_BYTES` | `2097152`（2MB） | 原始 body 上限 |
| `FETCH_URL_USER_AGENT` | `my-agent/1.0` | HTTP User-Agent |
| `FETCH_URL_MAX_CHARS_DEFAULT` | `32000` | 未传 `max_chars` 时使用 |

| 行为 | 说明 |
|------|------|
| 协议 | 仅 `http://`、`https://` |
| SSRF | 拒绝 localhost、私网、link-local、metadata（含 DNS 解析后 IP）；重定向最多 **5** 跳，每跳同样检查 |
| 内容类型 | `text/plain`、`text/markdown`、`application/json`、`application/xml` → 原文；`text/html` → stdlib `html.parser` 去标签转纯文本；其余（`image/*`、`application/pdf` 等）→ `unsupported_content_type` |
| 编码 | 尊重 `Content-Type` charset；缺省 UTF-8，`errors=replace` |
| JSON | **不**自动格式化，原文返回 |
| 截断 | 提取后超 `max_chars` 或原始 body 超 `FETCH_URL_MAX_BYTES` → 外层 `truncated: true` |
| `final_url` | 有重定向时与请求 `url` 不同；无重定向可与 `url` 相同 |

**错误码**：`invalid_url` · `blocked_host` · `timeout` · `too_large` · `unsupported_content_type` · `http_error` · `network_error`

```json
{
  "ok": true,
  "tool": "fetch_url",
  "data": {
    "url": "https://example.com/page",
    "final_url": "https://example.com/page",
    "content": "正文纯文本…",
    "content_type": "text/html"
  },
  "truncated": false,
  "error": null,
  "duration_ms": 890
}
```

### 7.6 `run_evolved`

| 项 | 值 |
|----|-----|
| 参数 | `tool_name`, `arguments`, `dry_run?` |
| 执行 | `python evolve/tools/.../main.py`，stdin JSON |
| 脚本约定 | exit 0 + stdout 一行 JSON |

```text
stdin:  { "path": "...", "dry_run": false }
stdout: { "ok": true, "written": "workspace/out.txt" }
stderr: 人类可读日志
```

---

## 8. 种子工具（M1a 建议随仓库提供）

| 工具 | 目录 | 作用 |
|------|------|------|
| `write_text` | `tools/common/write_text/` | 写 workspace；无此工具则 LLM 只能读不能改 |

主题专用种子（可选）：`tools/workflow/sort_downloads/` 等，按真实任务再加。

---

## 9. 工具质量（artifact，非调用）

- **Builtin**：少而稳；随内核单测  
- **Evolved**：薄封装成熟引擎；单一职责；fixture 可手测  
- **common** 只放真正跨主题的必备能力，避免变成第二个 builtin  

详见设计讨论：立项 → schema → 实现 → 真实任务验证；不堆一次性脚本。

---

## 10. 安全（与 PROJECT §6.4 一致）

- **无进程沙箱**；`workspace_only` 为约定
- `run_evolved` 不可关闭 confirm
- 日志不记密钥与完整大文件内容

---

## 11. 与 Skill / Memory 的边界

| 能力 | 说明 |
|------|------|
| Memory / Prompt | 主题路由；见 MEMORY.md |
| Evolved tool | 主题目录 + common；清单随 session 过滤 |
| Skill（M4+） | 编排已有 tool 名；不增加 Builtin 数量 |

---

## 12. 目录（实现后）

```text
agent-core/tools/
├── registry.py       # 扫描 evolve/tools/**；合并 6 builtin
├── executor.py
├── builtin/
│   ├── read_file.py
│   ├── list_dir.py
│   ├── grep.py
│   ├── web_search.py
│   ├── fetch_url.py
│   └── run_evolved.py
└── schema.py

evolve/
├── _index.toml
└── tools/
    ├── common/write_text/
    └── <topic>/<name>/
```

---

## 13. 决议摘要（v0.2.0）

| # | 议题 | 决议 |
|---|------|------|
| 1 | Builtin 数量与名单 | **6 个**：read/list/grep/web_search/fetch_url/run_evolved |
| 2 | Evolved 暴露 | **仅** `run_evolved`；清单按主题 + common 注入 system |
| 3 | 主题索引 | 统一 **`evolve/_index.toml`** |
| 4 | 跨主题工具 | **`tools/common/`**，每 session 都列入清单 |
| 5 | 本地搜 vs 上网搜 | **分开**；`grep` 与 `web_search` 并存 |
| 6 | confirm 交互 | `y/n`；`a` 仅 **workspace_only evolved** 本会话免确认；log `session_workspace_approved` |
| 7 | `web_search` 后端 | 默认 **DeepSeek**（Anthropic 子调用 + `LLM_API_KEY`）；可选 `brave`；§7.4 schema 不变 |
| 8 | `fetch_url` 实现 | **`httpx`**；stdlib HTML 去标签；SSRF；默认 32k chars / 2MB raw；§7.5 |
| 9 | 超长 tool 结果 | **>8k** 落盘 `tool_outputs/`；LLM 见 2k 预览 + `output_path` |

---

## 14. 验收（TOOLS 设计阶段）

- [ ] 6 Builtin 职责无重叠、无缺口（本地看 / 网络看 / 执行）
- [ ] Evolved 主题目录 + common 规则清楚
- [ ] 与 `evolve/_index.toml`、MEMORY 主题路由一致
- [ ] `run_evolved` + 会话清单可实现
- [ ] M1 **不做** skill

实现验收见 `TASKS.md` Phase 1～2。

---

## 15. 文档索引

| 文档 | 内容 |
|------|------|
| [MEMORY.md](./MEMORY.md) | 主题路由、三件套、`_index.toml` |
| [TASKS.md](./TASKS.md) | 实施 task |
| [PROJECT.md](./PROJECT.md) | 总览 |
