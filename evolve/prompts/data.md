# 数据处理

> 用户扩展主题 · T-805 示例（`data` + `csv_head`）

## 范围

- workspace 下的 CSV / 表格类文件预览与轻量分析
- 只读操作为主；写入 workspace 仍用 common 工具

## 硬规则

- `path` 均相对 **workspace**；`workspace_only` 工具不得越界
- 大文件优先用 `csv_head` 预览，不要一次性 `read_file` 整表
- 新增 data 工具放 `evolve/tools/data/<name>/`，`topics = ["data"]`

## evolved 工具（data 主题）

| 工具 | 作用 |
|------|------|
| `csv_head` | 预览 CSV 前 N 行（含表头），列名、总行数、推断列类型 |

## 新建 data 工具

1. 确认会话含 **data** 主题（`换主题` 或 S3 确认）。
2. `run_evolved` → `write_evolve`：**先** `evolve/tools/data/<name>/main.py`，**再** `tool.toml`。
3. **`path` + `content_base64` 放在 `run_evolved` 顶层**（与 `tool_name` 同级），`arguments` 用 `{}`；**禁止**把 TOML 塞进 `arguments.content`。
4. 备选：先 `write_text` 到 `workspace/`，再 `content_workspace_path`。
