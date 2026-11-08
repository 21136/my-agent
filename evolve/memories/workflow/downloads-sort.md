---
id: downloads-sort
topics: [workflow]
status: active
summary: workspace 下载夹按扩展名整理：按 workflow 主题分类的历史资产；工具执行由 executor 与逐次授权决定，先 dry_run 再执行
use_count: 5
last_used_at: "2026-08-09T00:41:37.812629Z"
---

## 场景

workspace 里有个「待整理」目录（模拟下载夹、收件箱），顶层混有 `.pdf` / `.jpg` / `.txt` 等文件，需要按扩展名自动分子文件夹。

## 工具

本条的 `workflow` 是历史/资产分类，不是会话加载或工具权限开关。需要时可通过 Builtin `run_evolved` 调用 evolved 工具 **`sort_by_extension`**（`evolve/tools/workflow/sort_by_extension/`）；是否执行由现有 executor 策略及每次调用的授权决定，主题不会获取或扩大权限。

| 参数 | 说明 |
|------|------|
| `path` | 相对 **workspace** 的目录路径（如 `_downloads_inbox`） |
| `include_hidden` | 默认 `false`；要处理以 `.` 开头的文件时设 `true` |
| `dry_run` | 先 `true` 预览 `moved[]`，确认后再正式执行 |

## 行为要点

- 只处理该目录**顶层文件**；已有子目录（如 `pdf/`）不递归深入。
- 扩展名 → 子文件夹：`report.pdf` → `pdf/report.pdf`；无扩展名 → `_no_ext/`。
- 目标重名时自动 `name-1.ext` 重命名。
- `workspace_only`：仅能在 workspace 下操作；执行前经 confirm（或本会话 `a` 免确认）。

## 推荐流程

1. 按工具索引/名称发现 `sort_by_extension`；无需设置或确认 session 主题。
2. `run_evolved` + `dry_run: true` → 核对 `moved` 列表。
3. 按现有 executor 授权流程逐次授权后，`dry_run: false` 正式移动。
4. 用 `list_dir` 抽查子目录结果。

## CLI 快测（PowerShell）

```powershell
cd D:\my-agent\agent-core
python my-agent tool run evolved sort_by_extension --json '{\"path\":\"_downloads_inbox\"}' --dry-run -y
```

## 关联

- 工具实现：T-502 `evolve/tools/workflow/sort_by_extension/`
- 主题提示：`evolve/prompts/workflow.md`
- 设计样例：MEMORY.md §5.2 `downloads-sort` 索引行
