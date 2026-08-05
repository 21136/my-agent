## 工具范例调研（追加块）

本次任务与 **evolved 工具 scaffold** 相关。在通用 explore 规则之上：

### 读什么（按顺序）

1. **`evolve/tool-catalog/INDEX.md`**（或已注入 catalog）— 现有工具是否已覆盖？
2. 若需新工具：读 1 个 **宽** reference（`run_command` · `write_text` · 同 scope 最通用者）。
3. 可选：读 1 个 **窄反例**（若有）说明为何不应照抄。

### 输出必须包含

0. **范围建议**：`不造` / `用现有 X+参数` / `可造` + 理由（一句话）
1. **schema 模式**：required 是否够 **宽**（可调 path/pattern/command）
2. **main.py 结构**：入口、错误 JSON、有无硬编码路径
3. **demo 质量**：是否测 **多组参数**；空跑 → 标注 fail
4. **write_evolve 建议**：scope 目录、工具名、draft、INDEX 一行描述（含适用范围）

### 禁止

- 不要输出完整 main.py 源码让父代理盲抄；给 **模式** 与 **差异点**。
- 不要调用 write_evolve。
