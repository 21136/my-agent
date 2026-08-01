# 写操作路径放开设计（WRITE-SCOPE）

> 版本 **0.2.0** · 2026-07-30  
> 状态：`implemented` — `resolve_under_agent_for_write` + deny-list 已落地；evolved 写/整理/exec 工具已切；`workspace_only` → `allow_approve_all`  
> 关联：[TOOLS.md](./TOOLS.md) · [MAP.md](./MAP.md) · [UX-POLISH.md](./UX-POLISH.md) · [TOOL-RETRY.md](./TOOL-RETRY.md)

---

## 0. 落地摘要（2026-07-30）

| 项 | 状态 |
|----|------|
| `paths._WRITE_DENY_PATTERNS` + `resolve_under_agent_for_write` | **done** |
| common/workflow 写工具改用新解析器 | **done** |
| exec 类改 `resolve_under_agent` | **done** |
| `allow_approve_all` + 确认文案「本会话 agent root 均允许」 | **done** |
| `write_evolve` 专用路径 | **未改**（仍限 evolve/tools） |

以下正文为设计真源；**实现以 §0 与代码为准**。

## 1. 动机

### 1.1 现状痛点

所有 evolved 写工具硬编码 `resolve_under_workspace()`——只能写入 `workspace/`，不能碰 agent root 下其他目录。

例如要 agent 帮忙在 `agent-core/` 里改一段 Python 代码、在 `evolve/` 里修一个 prompt、在 `desktop/src/` 里改一个 TypeScript 文件——这些路径 `read_file`/`grep` 都能读，但 `write_text`/`append_text` 写不了。

**这不是安全措施，是绊脚石。** 安全应该靠 deny-list 精确拦截少数危险路径（`.git/`、`.env`、`node_modules/`），而不是把所有非 workspace 的路径一竿子打死。

### 1.2 受影响工具（17 个）

| 类别 | 工具 | 当前 |
|------|------|------|
| **写文件** | `write_text` `append_text` `copy_move` `move_to_trash` | 只能写 `workspace/` |
| **执行** | `run_python` `npm_exec` `mvn_exec` `jshell_exec` `git_clone` `pip_install` `repl` | 只能在 `workspace/` 内操作 |
| **整理** | `flatten_dir` `dedupe_by_name` `archive_by_date` `study_note` | 只能操作 `workspace/` 内的目录 |
| **数据** | `csv_head` `ws_probe_tool`（只读） | 只能读 `workspace/` 内的文件 |

### 1.3 设计目标

| 目标 | 说明 |
|------|------|
| **全 agent root 可写** | 所有 agent root 下的非 deny-list 路径均可写入 |
| **精确 deny-list** | 只拦截真正危险的路径（`.git/`、`.env`、`data/sessions/` 等） |
| **安全边界不变** | agent root 外仍然写不进去；host 走独立路径 |
| **最小改动** | 只改 `paths.py` 加 deny-list + 各工具 `main.py` 改一行 |
| **confirm 无退化** | session `a`（本会话均允许）仍然有效，作用域从 workspace 扩展到 agent root |

---

## 2. 安全分析

### 2.1 deny-list

| 路径模式 | 理由 |
|----------|------|
| `.git/` 及子路径 | 破坏版本历史，不可逆 |
| `data/sessions/` 及子路径 | 会话数据，只能通过 `session.py` API 操作 |
| `.env`（任意深度） | 密钥泄露风险 |
| `node_modules/`（任意深度） | 包管理器产物，手工编辑无意义 |
| `__pycache__/`（任意深度） | Python 字节码缓存，无意义 |
| `*.pyc` | 同上 |
| `.pytest_cache/` | 测试缓存 |
| `dist/` `dist-electron/` `build/`（一级子目录） | 构建产物，手工编辑无意义 |

### 2.2 只读不变

`read_file`、`list_dir`、`grep` 保持 `resolve_under_agent`，不做 deny-list——**读不拦截，能看就能改。**

### 2.3 agent root 外

`resolve_under_agent` 本身已防止越界到 agent root 外（通过 `..` 穿越等）。host scope 走 `resolve_under_host` 独立路径，不受影响。

### 2.4 `write_evolve` 不做改动

`write_evolve` 有自己专用的路径校验（仅限 `evolve/tools/` 下注册 scope），不经过 `resolve_under_workspace`。

---

## 3. 方案

### 3.1 `paths.py` 加 deny-list + 新解析函数

```python
# 写操作 deny-list（精确拦截）
_WRITE_DENY_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"(^|[/\\])\.git([/\\]|$)"),
    re.compile(r"(^|[/\\])data[/\\]sessions([/\\]|$)"),
    re.compile(r"(^|[/\\])node_modules([/\\]|$)"),
    re.compile(r"(^|[/\\])__pycache__([/\\]|$)"),
    re.compile(r"(^|[/\\])\.pytest_cache([/\\]|$)"),
    re.compile(r"(^|[/\\])dist([/\\]|$)"),
    re.compile(r"(^|[/\\])dist-electron([/\\]|$)"),
    re.compile(r"(^|[/\\])build([/\\]|$)"),
)

def is_path_denied_for_write(rel_path: str) -> bool:
    """Check if a resolved agent-relative path is denied for writes."""
    for pattern in _WRITE_DENY_PATTERNS:
        if pattern.search(rel_path):
            return True
    # deny .env anywhere under agent root
    if rel_path.endswith(".env") or rel_path.endswith("/.env"):
        return True
    return False
```

`resolve_under_agent_for_write(raw)` 先走 `resolve_under_agent`，再拿 agent-relative 路径查 deny-list，命中则抛专用错误。

### 3.2 各工具 main.py 改动（一行改一个）

| 工具 | 当前 | 改后 |
|------|------|------|
| `write_text/main.py:58` | `resolve_under_workspace(path_arg)` | `resolve_under_agent_for_write(path_arg)` |
| `append_text/main.py:54` | 同上 | 同上 |
| `copy_move/main.py:106-107` | `resolve_under_workspace(source)` / `(dest)` | 同上 |
| `move_to_trash/main.py:72-73` | `resolve_under_workspace(path)` / `(trash)` | 同上 |
| `flatten_dir/main.py:94` | 同上 | 同上 |
| `dedupe_by_name/main.py:67` | 同上 | 同上 |
| `archive_by_date/main.py:78` | 同上 | 同上 |
| `study_note/main.py:110-111` | 同上 | 同上 |
| `run_python/main.py:47` | `resolve_under_workspace(text)` | `resolve_under_agent(text)` |
| `git_clone/main.py:144` | 同上 | 同上 |
| `npm_exec/main.py:60` | 同上 | 同上 |
| `mvn_exec/main.py:60` | 同上 | 同上 |
| `csv_head/main.py:98` | 同上 | `resolve_under_agent` |
| `ws_probe_tool/main.py:39` | 同上 | `resolve_under_agent` |

### 3.3 Confirm 流调整

当前 `workspace_only` 决定三个行为：
1. session `a`（本会话均允许）是否可选
2. `workspace_only=false` 的工具没有 `a` 选项，每次都要 confirm

改动：
- `workspace_only` 重命名为 `allow_approve_all`（更准确的语义）
- 默认 `true`（写 agent root 内的工具都允许 session `a`）
- `write_evolve` 保持在 `false`（每次 confirm，因为改 evolve 是高风险操作）
- executor 中相关判断从 `workspace_only` 改为 `allow_approve_all`
- `WS bridge` 中 "本会话 workspace 均允许" 改为 "本会话 agent root 均允许"

### 3.4 `tool.toml` manifest 字段

`workspace_only` → 新增别名兼容。`registry.py` 加载时同时识别 `workspace_only` 和 `allow_approve_all`，优先新名。

### 3.5 不动的部分

| 模块 | 理由 |
|------|------|
| `write_evolve` | 自有限制——仅 `evolve/tools/` 下注册 scope，专注进化写入 |
| `host_*` 工具 | 走独立 host scope 路径 |
| builtin `read_file`/`list_dir`/`grep` | 只读，已覆盖 agent root |
| `pip_install`/`jshell_exec`/`repl` | 分析后发现不涉及文件路径参数，无改动 |

---

## 4. 实施计划

| # | 步骤 | 文件 | 验收 |
|----|------|------|------|
| 1 | `paths.py` 加 `_WRITE_DENY_PATTERNS` + `is_path_denied_for_write()` + `resolve_under_agent_for_write()` | `agent-core/paths.py` | `python agent-core/paths.py` 通过，含 deny-list 验收 |
| 2 | 4 个 common 写工具改 `resolve_under_workspace` → `resolve_under_agent_for_write` | `evolve/tools/common/` 下各 `main.py` | 每个 `python .../main.py demo` 通过 |
| 3 | 4 个 workflow 整理工具同上 | `evolve/tools/workflow/` | 同上 |
| 4 | 4 个 exec 工具改 `resolve_under_workspace` → `resolve_under_agent` | `evolve/tools/common/run_python/` 等 | 同上 |
| 5 | 2 个 data 工具同上 | `evolve/tools/data/` | 同上 |
| 6 | `workspace_only` → `allow_approve_all` 兼容迁移 | `registry.py` + `executor.py` + 各 `tool.toml` | `python agent-core/agent.py` 通过 |
| 7 | 确认文案更新 | `executor.py` + `server.py` | 按钮文案显示 "本会话 agent root 均允许" |
| 8 | 全量回归 | 全部 demo + `pytest tests/` | 186 通过 |

---

## 5. 风险

| 风险 | 缓解 |
|------|------|
| LLM 误删或误写 `evolve/` 关键文件 | `write_evolve` 保持每次 confirm；commit 习惯保证 Git 可回滚 |
| LLM 写垃圾到 `agent-core/` | confirm 仍是默认行为；session `a` 需用户主动点击 |
| deny-list 遗漏 | 后续可追加；`.git/` 拦截已覆盖最危险场景 |

---

## 6. 验收

### 6.1 自动化

```powershell
cd D:\my-agent\agent-core
python paths.py                     # deny-list 验收
python ..\evolve\tools\common\write_text\main.py demo
python ..\evolve\tools\common\append_text\main.py demo
python ..\evolve\tools\common\copy_move\main.py demo
python ..\evolve\tools\common\move_to_trash\main.py demo
python ..\evolve\tools\workflow\flatten_dir\main.py demo
python ..\evolve\tools\workflow\dedupe_by_name\main.py demo
python ..\evolve\tools\workflow\archive_by_date\main.py demo
python ..\evolve\tools\workflow\study_note\main.py demo
python ..\evolve\tools\common\run_python\main.py demo
python ..\evolve\tools\common\git_clone\main.py demo
python ..\evolve\tools\common\npm_exec\main.py demo
python ..\evolve\tools\data\csv_head\main.py demo
python ..\evolve\tools\data\ws_probe_tool\main.py demo
python tools/registry.py
python agent.py
```

### 6.2 手工验收

| 场景 | 预期 |
|------|------|
| agent 写 `agent-core/main.py`（agent root 内） | 成功写入 |
| agent 写 `.git/config` | 拒绝，deny-list 命中 |
| agent 写 `data/sessions/xxx/meta.json` | 拒绝 |
| agent 写 `workspace/outside` → 路径穿透到 agent root 外 | `PathOutOfBoundsError` |
| agent 写 `evolve/prompts/safety.md` | 成功写入 |
| agent 写 `.env` | 拒绝 |
| 确认卡按钮 | "本会话 agent root 均允许" |

---

## 7. 记录

| 日期 | 变更 |
|------|------|
| 2026-07-29 | 初稿。分析 17 个受影响工具，deny-list 设计。 |
| 2026-07-30 | **实施完成**：paths + evolved 工具 + allow_approve_all；文档标 `implemented`。 |
