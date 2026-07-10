---
id: downloads-sort
topics: [workflow]
status: active
summary: workspace 下载夹按扩展名整理：workflow 主题下用 sort_by_extension，先 dry_run 再执行
use_count: 1
last_used_at: "2026-07-10T06:59:25.764140Z"
---

## 场景

workspace 里有个「待整理」目录（模拟下载夹、收件箱），顶层混有 `.pdf` / `.jpg` / `.txt` 等文件，需要按扩展名自动分子文件夹。

## 工具

确认 **workflow** 主题后，用 evolved 工具 **`sort_by_extension`**（`evolve/tools/workflow/sort_by_extension/`），经 Builtin `run_evolved` 调用。

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

1. 确认 session 主题为 `workflow`（system 清单含 `sort_by_extension`）。
2. `run_evolved` + `dry_run: true` → 核对 `moved` 列表。
3. `dry_run: false` 正式移动。
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
