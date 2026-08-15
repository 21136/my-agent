# 工具系统设计（TOOLS）

> 版本 **0.3.2** · 2026-08-06 · 与 `MEMORY.md`（双索引）配套  
> **工具发现 / 执行面（Phase 23）**：[TOOL-CATALOG.md](./TOOL-CATALOG.md) — **取消主题硬锁**；每轮注入 INDEX，细则读桶  
> **现行写入边界**：[WRITE-SCOPE.md](./WRITE-SCOPE.md)；参数自修正：[TOOL-RETRY.md](./TOOL-RETRY.md)  
> M1a 设计文档演进；实现以代码 + 上述文档为准

---

## 1. 目标

定义 my-agent 的 **唯一执行面**：LLM 只能通过 **已注册的 tool** 读信息或改变文件系统，不得假装执行。

设计约束（已决）：

| 约束 | 说明 |
|------|------|
| **Builtin 分两类共 11 个** | **核心 7**（观察/执行口）+ **编排 4**（子代理/上下文）；核心长期不随使用增长 |
| **Evolved 按主题目录放置** | `evolve/tools/<topic>/` + `evolve/tools/common/`（**磁盘组织**；非调用门禁） |
| **执行唯一口** | 所有 evolved 经 `run_evolved`；**例外（Phase 41）**：`run_command` · `write_text` · `patch_file` 可向 LLM 扁平原语暴露，内核仍路由到 `run_evolved`（见 [AGENT-HARNESS.md](./AGENT-HARNESS.md)） |
| **~~主题过滤清单~~ → 目录 INDEX** | **superseded（Phase 23）**：凡 `status=active` 均可调；system 注入短 [INDEX](../evolve/tool-catalog/INDEX.md)，不按 `topics[]` 硬锁。详见 [TOOL-CATALOG.md](./TOOL-CATALOG.md) |

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
| **主题与记忆共用索引** | `evolve/_index.core.toml` + `evolve/_index.user.toml` 合并（见 [EXTENSIONS.md](./EXTENSIONS.md)）；**与**工具目录 `evolve/tool-catalog/INDEX.md` **并存**——前者管 prompt/memory，后者管工具发现；system 每轮**两者都注入** |

---

## 3. Builtin（11 个 function，不分主题）

**始终**暴露给 LLM；任何 session 都可用。不按主题过滤。分两类：

### 3.0 核心 7 个（观察 / 执行口）

| name | 作用 | confirm | dry-run |
|------|------|---------|---------|
| `read_file` | 读文本文件（有大小上限） | 否 | 否 |
| `list_dir` | 列目录（可递归一层） | 否 | 否 |
| `grep` | 在路径下搜**本地**文件内容 | 否 | 否 |
| `glob_file_search` | 按 glob 列文件（Phase 42 · **done**） | 否 | 否 |
| `web_search` | **上网**搜索（query → 标题/链接/摘要） | 否 | 否 |
| `fetch_url` | 拉取指定 URL 正文（文本/markdown） | 否 | 否 |
| `run_evolved` | 调用 `evolve/tools/` 已注册脚本 | **是** | 透传 |

### 3.0a 编排 4 个（子代理 / 上下文）

| name | 作用 | confirm | dry-run |
|------|------|---------|---------|
| `propose_context_switch` | 提议切换 project/shell 上下文 | **是** | 否 |
| `plan_partner` | 计划子代理（TASKS/MAP/PROJECT/ENV · 侧栏采纳） | 否 | 否 |
| `deliverable_review` | 只读交付物审查子代理（绑定项目） | 否 | 否 |
| `explore` | 只读 explore 子代理（须显式 task/scope） | 否 | 否 |

Builtin 代码在 `agent-core/tools/builtin/`；**不**放在 `evolve/tools/`。注册表见 `tools/registry.py` 的 `BUILTIN_TOOLS`。

### 3.1 观察 vs 执行

```text
本地观察：read_file · list_dir · grep · glob_file_search
编排：propose_context_switch · plan_partner · deliverable_review · explore
网络观察：web_search · fetch_url
动手执行：run_command · write_text · patch_file（扁平原语，Phase 41）
            或 run_evolved → 其它 evolve/tools/*
```

### 3.1a 扁平原语 proxy（Phase 41 · AGENT-HARNESS P1）

| name | 路由 | 说明 |
|------|------|------|
| `run_command` | `run_evolved` → `run_command` | 与 manifest 一致；**优先**于嵌套 `run_evolved` |
| `write_text` | `run_evolved` → `write_text` | 整文件写入 |
| `patch_file` | `run_evolved` → `patch_file` | 行号/锚点补丁 |

实现：`agent-core/tool_proxies.py`；**封顶 3 个**；ask 模式与 `run_evolved` 一并隐藏。

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
├── _index.core.toml         # 主题索引（core）
├── _index.user.toml         # 主题索引（user 扩展）
├── tool-catalog/
│   └── INDEX.md             # 工具目录 L0（与主题索引并存注入 system）
├── tools/
│   ├── common/              # 跨主题；active 均可调（Phase 23 起非调用门禁）
│   │   ├── write_text/
│   │   ├── append_text/     # T-505
│   │   ├── copy_move/
│   │   └── move_to_trash/
│   ├── coding/
│   │   └── <tool_name>/
│   └── workflow/
│       ├── sort_by_extension/
│       ├── rename_batch/      # T-506
│       ├── flatten_dir/
│       ├── dedupe_by_name/
│       └── archive_by_date/
```

| 路径 | 含义 |
|------|------|
| `evolve/tools/common/<name>/` | 跨主题必备（`write_text`、`append_text`、`copy_move`、`move_to_trash` 等）；目录约定 |
| `evolve/tools/<topic>/<name>/` | 按主题文件夹放置；**Phase 23 起不再**「仅确认该 topic 才可调用」 |

由你在放入前 **审阅源码**；内核不自动生成。`status: active` 才可通过 `run_evolved` 调用（CLI 调试可跑任意 status，见 §7）。`suspect` / `archived` 不进执行面。

### 4.2 与主题索引的关系

索引由 [MEMORY.md](./MEMORY.md) 定义；启动时合并 `_index.core.toml` + `_index.user.toml`（见 [EXTENSIONS.md](./EXTENSIONS.md)）。每个 topic 增加 `tool_dirs`：

```toml
[[topic]]
id = "coding"
prompt = "prompts/coding.md"
memory_dirs = ["memories/coding"]
tool_dirs = ["tools/coding"]
```

`evolve/tools/common/` **不**写在 topic 的 `tool_dirs` 里；磁盘组织约定，**执行面**凡 `status=active` 均可调（见 [TOOL-CATALOG.md](./TOOL-CATALOG.md)）。

**双索引注入（Phase 23）**：`build_system_prompt` 同时注入 `[主题索引]`（来自 `_index.*.toml`）与 `[工具索引]`（来自 `tool-catalog/INDEX.md` + 能力提示）。主题确认仍管 prompt/memory，**不管** evolved 执行门禁。

### 4.3 会话内 LLM 可见的 evolved 导引（Phase 23）

设计文档场景先读 `evolve/tool-catalog/buckets/design.md`，再通过 `run_evolved` 调用 `design_document`；该工具支持 Markdown 与 DOCX。

> **superseded**：旧「按主题列全量 name+description 清单」已废止。现行见 [TOOL-CATALOG.md](./TOOL-CATALOG.md)。

Builtin 恒为 **11** 个 function（核心 7 + 编排 4）；evolved **不**平铺为独立 function，经 `run_evolved` 调用：

- **执行面**：凡 registry 中 `status=active` 的 evolved 均可调（**不**按 `meta.topics` 硬锁）。
- **每轮 system**：注入短文档 `evolve/tool-catalog/INDEX.md`（桶路径表）；细则按需 `read_file` 对应 `buckets/*.md`。
- **主题确认**（MEMORY §4）仍加载 **prompt / memory**；**不再**决定「能调哪些工具」。
- CLI `tool run` 仍不受主题限制。

历史示例（已废弃，勿再实现为硬锁）：

```text
[本会话可用 evolved 工具]（调用 run_evolved.tool_name）
## common（始终）
- write_text: …
## coding（本会话主题）
- format_py: …
```

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
allow_approve_all = true   # 旧名 workspace_only（仍可读兼容）
timeout_sec = 60
```

内核扫描 `evolve/tools/**/tool.toml`（含 `common/` 与各 topic 子目录）。

---

## 6. 调用流程

```text
用户输入
    → LLM 选 Builtin（11 个之一）或 run_evolved / 扁平原语 proxy
    → ToolExecutor.validate（schema、路径、策略）
    → run_evolved：tool_name 须在「本会话清单」或 CLI 显式指定
    → 若 confirm：预览，等待 y/n/a（§6.3）
    → 若 dry_run：透传 evolved
    → 执行 → evolve_log → 回填 LLM（§6.4 超长结果落盘）
    → 可恢复参数错误：agent 可自修正一次（TOOL-RETRY），不计入工具配额
```

### 6.3 Confirm 交互（已决 · 2026-07-30 更新）

| 输入 | 行为 |
|------|------|
| `y` | 执行本次 |
| `n` | 拒绝 |
| `a` | **仅** `allow_approve_all=true` 的 **evolved** tool：本会话后续同类调用 **免 confirm**（作用域 = **agent root**，deny-list 仍生效）；写 log；记入 session 批准状态 |

Builtin **无** `a`；`allow_approve_all=false` 的 evolved **无** `a`。  
旧字段名 `workspace_only` 仍可读，语义等同 `allow_approve_all`。

### 6.4 超长 tool 结果落盘（已决，对齐 Cursor）

单次 tool 结构化结果（序列化后）**> 8000 字符**时：

```text
1. 全文写入 data/sessions/<id>/tool_outputs/<uuid>.txt
2. 返回 LLM：前 2000 字符 + output_path + truncated: true
3. LLM 可 read_file / grep 续读
```

适用于 **Builtin 与 evolved**；阈值 env：`TOOL_OUTPUT_SPILL_CHARS`（默认 8000）、`TOOL_OUTPUT_PREVIEW_CHARS`（默认 2000）。

**失败结果（Phase 41 P4）**：`ok=false` 时若整段 envelope 超阈值，同样 spill；回灌 LLM 的 `error.details` 仅含 `preview` + `hint`（`read_file <output_path>`），避免 stderr 墙污染上下文。

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

### 7.3.1 `glob_file_search`（Phase 42 · **done**）

> 真源：[CURSOR-GAP-NEXT.md](./CURSOR-GAP-NEXT.md) §3 · TASKS **T-4221** · 实现 `agent-core/tools/builtin/glob_file_search.py`

| 项 | 值 |
|----|-----|
| 参数 | `pattern`（glob，如 `**/*.py`）, `path`（默认 `.`）, `max_results?`（默认 200，硬顶 1000）, `ignore_case?` |
| 实现 | 优先 `rg --files -g`；回退 `pathlib` + fnmatch |
| 返回 | `{ paths: string[], truncated: bool }` |
| confirm | **否**（只读，与 grep 同级） |
| gitignore | 尊重 `.gitignore`；跳过 `node_modules` 等（IT-432） |
| defer | 语义搜 → 独立 `CODEBASE-SEARCH.md` |

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
| **`write_evolve` 快捷字段**（与 `tool_name` **同级**，合并进内层 `arguments`） | `path`, `content_base64`, `content_workspace_path`, `on_conflict` |
| 执行 | `python evolve/tools/.../main.py`，stdin JSON |
| 脚本约定 | exit 0 + stdout 一行 JSON；**失败时** stdout 仍可为 `{"ok":false,"error":"..."}`，执行器会解析该 JSON（不只看 stderr） |

`write_evolve` 推荐形态（避免 `tool_calls` JSON 转义失败）：

```json
{
  "tool_name": "write_evolve",
  "path": "evolve/tools/common/foo/main.py",
  "content_base64": "<UTF-8 标准 base64>",
  "on_conflict": "overwrite",
  "arguments": {},
  "dry_run": false
}
```

| `on_conflict` | 行为 |
|---------------|------|
| `overwrite` | 覆盖已有文件（**造工具推荐**） |
| `rename` | 写入 `main-1.py` 等 |
| `skip`（默认） | 目标已存在 → **`ok: false`**，提示改用 `overwrite` / `rename`（`dry_run` 预览仍返回 `skipped: true`） |

**参数合并（coalesce）**：`tool_name == "write_evolve"` 时，顶层 `path` / `content_base64` / `content_workspace_path` / `on_conflict` 会并入内层 `arguments`；**同名 key 已在内层 `arguments` 里则内层优先**（推荐顶层传、内层 `{}`）。`dry_run` 顶层为 `true` 时优先；否则可读内层布尔值。

**执行器预检（P1）**：`tool.toml` 必须 `content_base64` 或 `content_workspace_path`；`main.py` / `README.md` 含换行、双引号或较长正文时禁止 plain `content`。scaffold 回合禁止 `write_text` 写脚手架**文件名**（`main.py` / `tool.toml` / `README.md`），可写 `workspace/_staging*` 暂存；非 scaffold 回合仅拒绝 `evolve/tools/<scope>/<name>/` 下上述三文件路径（`workspace/` 内同名文件如项目 `README.md` **允许**）。

**`dry_run` 优先级**：`run_evolved` 顶层 `dry_run: true` 时始终预览；顶层为 `false` 或未设时，可读内层 `arguments.dry_run` 布尔值。

```text
stdin:  { "path": "...", "dry_run": false }
stdout: { "ok": true, "written": "evolve/tools/common/foo/main.py" }
stderr: 人类可读日志（通常为空；错误优先在 stdout JSON）
```

---

## 8. 种子工具（M1a 建议随仓库提供）

| 工具 | 目录 | 作用 |
|------|------|------|
| `write_text` | `evolve/tools/common/write_text/` | 写 workspace；无此工具则 LLM 只能读不能改 |
| `copy_move` | `evolve/tools/common/copy_move/` | workspace 内复制/移动文件或目录（T-505） |
| `move_to_trash` | `evolve/tools/common/move_to_trash/` | 移入 `_trash/` 软删除（T-505） |
| `write_evolve` | `evolve/tools/common/write_evolve/` | 向 `evolve/tools/<scope>/<name>/` 写 `tool.toml` / `main.py`（T-508；进化落地） |
| `git_clone` | `evolve/tools/common/git_clone/` | 浅克隆 https 公开仓到 `workspace/` 或 `evolve/tools/`（T-1115） |
| `project_catalog` | `evolve/tools/common/project_catalog/` | 项目列表 + session_id + 跨壳查阅指引（T-1117） |

主题专用种子（workflow，T-506）：

| 工具 | 目录 | 作用 |
|------|------|------|
| `sort_by_extension` | `evolve/tools/workflow/sort_by_extension/` | 按扩展名分子文件夹（T-502） |
| `rename_batch` | `evolve/tools/workflow/rename_batch/` | 批量重命名顶层文件 |
| `flatten_dir` | `evolve/tools/workflow/flatten_dir/` | 子目录文件提升到顶层 |
| `dedupe_by_name` | `evolve/tools/workflow/dedupe_by_name/` | 按文件名报告重复（只读） |
| `archive_by_date` | `evolve/tools/workflow/archive_by_date/` | 按日期归档到 `YYYY-MM/` |

**coding**（T-507）：

| 工具 | 目录 | 作用 |
|------|------|------|
| `run_demo` | `evolve/tools/coding/run_demo/` | 运行 `agent-core/` 下 Python 验收脚本 |
| `run_tests` | `evolve/tools/coding/run_tests/` | 按 suite 批量跑 demo（quick / core / governance / evolve / all） |
| `git_snapshot` | `evolve/tools/coding/git_snapshot/` | 只读 git status + diff --stat |
| `patch_file` | `evolve/tools/coding/patch_file/` | 行号/锚点文本补丁（agent 根；**仅改已有文件**） |

> **换行（BUG-025 · fixed T-4252）**：find / line_range 落盘经 `write_utf8_text`（LF 规范化 · 无平台换行翻译）。大文件仍优先 `_staging` + `content_workspace_path`（8192 内联上限）。详见 [bugs/2026-08-05-patch-file-crlf-corruption.md](./bugs/2026-08-05-patch-file-crlf-corruption.md)。

**data**（T-805，用户扩展主题）：

| 工具 | 目录 | 作用 |
|------|------|------|
| `csv_head` | `evolve/tools/data/csv_head/` | 预览 CSV 前 N 行、列类型推断、总行数 |

### 8.1 写入边界（WRITE-SCOPE · 2026-07-30）

| 区域 | 可用工具 | 说明 |
|------|----------|------|
| **agent root 内**（非 deny-list） | `write_text` / `patch_file` / `copy_move` / `move_to_trash` / `run_command` 等 active 工具 | `resolve_under_agent_for_write`；`allow_approve_all=true` 可 session `a` |
| **deny-list** | — | `.git/`、`data/sessions/`、`.env`、`node_modules/`、`__pycache__/` 等硬拒 |
| `evolve/tools/<scope>/<name>/` | **`write_evolve`** · **`git_clone`**（`target=evolve_tools`） | `write_evolve` 仅三件套；均 **无 `a`** |
| `agent-core/`、`docs/` 等已有文件 | `patch_file`（及通用写工具） | `patch_file` 不能创建新路径 |
| `evolve/prompts/`、`memories/` | proposal 接受路由 | 不经 tool 直写；见 EVOLVE §7 |
| agent root **外** | host 路径 | 走 host scope；与 WRITE-SCOPE 无关 |

详见 [WRITE-SCOPE.md](./WRITE-SCOPE.md)。

### 8.2 项目构建（PROJECT-MODE §0d · T-4310 后）

| 工具 | 参数 | 备注 |
|------|------|------|
| **`run_command`** | **`command`** + **`working_dir`**（可用别名 `cwd`） | 跑 `npm`/`mvn`/`python` 等；读 `ENV.md` 时 working_dir 指向 `workspace/<id>/…` |
| **`repair_node_modules`** | **`working_dir`** | 依赖损坏时显式重装（勿手写删 `node_modules`） |
| **`run_project_tests`** | **`working_dir`** · `suite` | 结构化测试 + Progress Gate `test` 证据 |

示例（项目内前端 build）：

```json
{
  "tool_name": "run_command",
  "arguments": {
    "working_dir": "workspace/<id>/frontend",
    "command": "npm run build"
  }
}
```

已有 `node_modules` 时默认拒 `npm install`（E9 · `force_install:true` 可覆盖）。详见 `agent-core/project_npm_guard.py`。

`npm_exec` / `mvn_exec` / `repl` 等已 **archived** — 见 [ARCHIVED-TOOLS.md](./ARCHIVED-TOOLS.md)。

项目模式下 **禁止** 用 archived `repl` 跑包管理（executor E8 硬拒；须 `run_command`）。详见 [PROJECT-MODE.md](./PROJECT-MODE.md) §0d。

**进化新工具闭环**：`记住`（可选 `tool_suggestion`）→ **`write_evolve` 先 `main.py` 再 `tool.toml`**（`status: active` 时 `write_evolve` 校验 `main.py` 已存在）→ **`tool.toml` 写盘前经 `parse_tool_manifest` 预检**（非法清单拒绝写入，避免 `ToolRegistry.load()` 启动失败）→ 成功写入 `tool.toml` 后**同会话内自动重载 registry**（新 `active` 工具立即可 `run_evolved`）。`registry` 仅对 `active`/`staged` 要求 entry script；`draft` 可仅有清单。多行或含 `"` 的正文优先 **`content_base64`**（UTF-8 标准 base64），避免 `tool_calls` JSON 转义失败；造工具时 **`on_conflict: overwrite`**。写完后建议 `git diff` + commit。

---

## 9. 工具质量（artifact，非调用）

- **Builtin**：少而稳；随内核单测  
- **Evolved**：薄封装成熟引擎；单一职责；fixture 可手测  
- **common** 只放真正跨主题的必备能力，避免变成第二个 builtin  

详见设计讨论：立项 → schema → 实现 → 真实任务验证；不堆一次性脚本。

---

## 10. 安全（与 PROJECT §6.4 一致）

- **无进程沙箱**；写范围靠 deny-list + confirm；`allow_approve_all` 为会话级约定
- `run_evolved` 不可关闭 confirm
- 日志不记密钥与完整大文件内容

---

## 11. 与 Skill / Memory 的边界

| 能力 | 说明 |
|------|------|
| Memory / Prompt | 主题路由；见 MEMORY.md |
| Evolved tool | 主题目录 + common（组织）；执行面 = active（[TOOL-CATALOG.md](./TOOL-CATALOG.md)） |
| Skill（M4+） | 编排已有 tool 名；不增加 Builtin 数量 |

---

## 12. 目录（实现后）

```text
agent-core/tools/
├── registry.py       # 扫描 evolve/tools/**；合并 11 builtin
├── executor.py
├── builtin/
│   ├── read_file.py
│   ├── list_dir.py
│   ├── grep.py
│   ├── glob_file_search.py
│   ├── web_search.py
│   ├── fetch_url.py
│   ├── run_evolved.py
│   ├── propose_context_switch.py
│   ├── plan_partner.py
│   ├── deliverable_review.py
│   └── explore.py
└── schema.py

evolve/
├── _index.core.toml
├── _index.user.toml
├── tool-catalog/
│   └── INDEX.md
└── tools/
    ├── common/
    │   ├── write_text/
    │   ├── copy_move/
    │   └── move_to_trash/
    └── <topic>/<name>/
```

---

## 13. 决议摘要（v0.2.0）

| # | 议题 | 决议 |
|---|------|------|
| 1 | Builtin 数量与名单 | **11 个**：核心 7（read/list/grep/glob/web/fetch/run_evolved）+ 编排 4（context_switch/plan_partner/deliverable_review/explore） |
| 2 | Evolved 暴露 | **仅** `run_evolved`（+ Phase 41 扁平原语）；导引 = INDEX + 桶（[TOOL-CATALOG.md](./TOOL-CATALOG.md)）；**不再**按主题过滤执行面 |
| 3 | 主题索引 | **`evolve/_index.core.toml` + `_index.user.toml`**（驱动 prompt/memory）；工具目录 = **`tool-catalog/INDEX.md`**（两者并存注入） |
| 4 | 跨主题工具 | **`evolve/tools/common/`** 仍为目录约定；active 均可调 |
| 5 | 本地搜 vs 上网搜 | **分开**；`grep` 与 `web_search` 并存 |
| 6 | confirm 交互 | `y/n`；`a` 仅 **allow_approve_all evolved** 本会话免确认（agent root） |
| 7 | `web_search` 后端 | 默认 **DeepSeek**（Anthropic 子调用 + `LLM_API_KEY`）；可选 `brave`；§7.4 schema 不变 |
| 8 | `fetch_url` 实现 | **`httpx`**；stdlib HTML 去标签；SSRF；默认 32k chars / 2MB raw；§7.5 |
| 9 | 超长 tool 结果 | **>8k** 落盘 `tool_outputs/`；LLM 见 2k 预览 + `output_path` |

---

## 14. 验收（TOOLS 设计阶段）

- [ ] 核心 7 Builtin 职责无重叠、无缺口（本地看 / 网络看 / 执行）；编排 4 与子代理文档一致
- [ ] Evolved 主题目录 + common 规则清楚
- [ ] 与 `evolve/_index.*.toml`、`tool-catalog/INDEX.md`、MEMORY 主题路由一致
- [ ] `run_evolved` + 会话清单可实现
- [ ] M1 **不做** skill

实现验收见 `TASKS.md` Phase 1～2。

---

## 15. 文档索引

| 文档 | 内容 |
|------|------|
| [TOOL-CATALOG.md](./TOOL-CATALOG.md) | Phase 23：INDEX / 桶 / 取消主题硬锁 |
| [MEMORY.md](./MEMORY.md) | 主题路由、三件套、`_index.core.toml` / `_index.user.toml` |
| [TASKS.md](./TASKS.md) | 实施 task |
| [PROJECT.md](./PROJECT.md) | 总览 |
