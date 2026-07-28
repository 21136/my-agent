# Git 拉取 / _vendor（GIT-VENDOR）

> 版本 0.1.0 · 2026-07-12 · 与 [TOOLS.md](./TOOLS.md) · [PROJECT-MODE.md](./PROJECT-MODE.md) 配套

---

## 1. 动机

助手造工具或做项目时，常需参考 **开源仓库**整仓代码，而不只是 `fetch_url` 拉单个文件。当前能力缺口：

| 现有能力 | 局限 |
|----------|------|
| `fetch_url` | 单页/单文件 raw，非整仓 |
| `pip_install` | 仅 Python 包；`git+https` 未验收 |
| `write_evolve` | 只能写三件套，不能拉依赖树 |
| 人手 `git clone` | 可行，但助手无法自主 |

本设计增加 evolved 工具 **`git_clone`**，在受控路径内浅克隆公开仓库。

---

## 2. 双目标（已定）

用户确认 **workspace 与 evolve/tools 两种落点都需要**：

| `target` | 落点 | 典型用途 |
|----------|------|----------|
| `workspace` | `workspace/<path>/` | 项目 **vendor** 依赖、参考实现、示例代码 |
| `evolve_tools` | `evolve/tools/<scope>/<name>/` | 以开源仓为起点 **造 evolved 工具**、vendor SDK |

**不**支持 clone 到 `agent-core/`、`evolve/prompts/`、`evolve/memories/` 等。

---

## 3. 工具契约

**名称**：`git_clone`（`evolve/tools/common/git_clone/`）

### 3.1 入参

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `url` | string | 是 | `https://` 公开仓库 URL（可不带 `.git` 后缀） |
| `dest` | string | 是 | 相对 agent 根的目标目录（见 §3.2） |
| `target` | string | 是 | `workspace` \| `evolve_tools` |
| `branch` | string | 否 | `--branch`；与 `tag` 互斥 |
| `tag` | string | 否 | 作为 `--branch` 传入（detached HEAD） |
| `depth` | int | 否 | 浅克隆深度，默认 `1` |
| `on_conflict` | string | 否 | `skip` \| `rename` \| `overwrite`，默认 `skip` |
| `dry_run` | bool | 否 | 只预览 `git clone` 命令 |

### 3.2 路径规则

**`target=workspace`**

- `dest` 解析为 `workspace/` 下路径（可写 `my-proj/vendor/foo` 或 `workspace/my-proj/vendor/foo`）
- 遵守 `paths.resolve_under_workspace` 边界

**`target=evolve_tools`**

- `dest` 必须为 `evolve/tools/<scope>/<name>/…`（`scope` 须在合并索引 `tool_dirs` 白名单内，含 `common`）
- 禁止 `..`；不要求目录已注册为 tool（可先 clone 再 `write_evolve` 补 `tool.toml`）

### 3.3 出参

`ok`, `command`, `dest`, `url`, `exit_code`, `stdout`, `stderr`, `truncated`, `dry_run`

---

## 4. 安全与策略

| 项 | 规则 |
|----|------|
| 协议 | 仅 `https://`（拒绝 `http://`、`file://`、`git@` SSH） |
| 主机白名单 | `github.com`, `gitlab.com`, `bitbucket.org`, `codeberg.org` 及子域 |
| 克隆 | 默认 `--depth 1`；`--single-branch` 当指定 branch/tag |
| confirm | **每次** confirm（`workspace_only=false`，**无** session `a` 免确认） |
| 超时 | `policy.timeout_sec = 300` |
| 许可证 | 不自动校验；助手应提醒用户注意 LICENSE |

---

## 5. 项目模式边界

| 场景 | 行为 |
|------|------|
| project 壳 + `target=evolve_tools` | **硬拒**（同 `write_evolve`：须切 grow 壳） |
| project 壳 + `target=workspace` + 计划未确认 | 仅允许 dest 为三件套同级目录时… **否**；vendor 属源码 → **须 `confirmed` 后** |
| project 壳 + `target=workspace` + 已确认 | dest 须在 `project_root` 下 |
| grow / daily | 两 target 均可（仍 confirm） |

---

## 6. 推荐用法

**项目 vendor**

```json
{
  "tool_name": "git_clone",
  "arguments": {
    "url": "https://github.com/org/lib.git",
    "target": "workspace",
    "dest": "cli-demo-proj/vendor/lib",
    "depth": 1,
    "dry_run": true
  }
}
```

**造工具前拉参考仓**

```json
{
  "tool_name": "git_clone",
  "arguments": {
    "url": "https://github.com/org/cool-cli.git",
    "target": "evolve_tools",
    "dest": "evolve/tools/common/cool_cli_ref",
    "depth": 1
  }
}
```

之后用 `grep` / `read_file` / `host_read` 阅读，再用 `write_evolve` 写精简版 `main.py`。

---

## 7. 任务

| ID | 内容 | 状态 |
|----|------|------|
| T-1114 | 本文档定稿 | done |
| T-1115 | `git_clone` 实现 + project 边界 + demo | done |

---

## 8. 远期（非本版）

- `commit` 精确检出、`sparse-checkout`
- 私有仓 + token（环境变量，不落盘）
- 克隆后自动写 `VENDOR.md` 记录 URL / 许可证
- `workspace_only=true` 拆分工具以便 session `a`（若用户强需求）
