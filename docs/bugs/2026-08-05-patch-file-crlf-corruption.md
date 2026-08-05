# BUG-025 · patch_file CRLF 增殖 + read/write 放大（「文件又乱了」恶性循环）

> **日期**：2026-08-05  
> **状态**：**fixed**（T-4252～4254 · IT-99 绿 · S-99 huiyi views normalize）  
> **关联**：[TOOLS.md](../TOOLS.md) §8 · [WRITE-SCOPE.md](../WRITE-SCOPE.md) · [BUG-024](./2026-08-05-inline-write-repeat-guard-loop.md) · [RUNTIME-GUARDS.md](../RUNTIME-GUARDS.md) G17 · `TASKS.md` T-4251～T-4255  
> **触发会话**：`workspace/huiyi` · `20260804-dcef2d2b` · `InsurancePolicyList.vue` / `DoctorList.vue` / `Layout.vue` 等  
> **用户决策**：2026-08-05 — **文档先行**；P0 修 `patch_file` find 写盘 + `write_text` 换行规范化。

---

## 1. 现象

1. 项目模式批量美化 huiyi 前端（8 个 `views/*.vue`）时，助手**同一天内 10+ 次**口语：「文件又乱了」「重写整个 xxx.vue」。
2. 磁盘上 Vue 文件出现 **`\r\r\r\r\r\n`** 行尾（例：`InsurancePolicyList.vue` 约 **213** 处 `\r\r`，117 行）。
3. `patch_file` **find** 锚点逐渐 `find anchor not found`；**line_range** 把 `<template #empty>` / `import` 插进 `<el-table>` 内或错误行号 → **结构损坏**。
4. 助手试图 `write_text` **整文件重写**恢复 → Vue 单文件常 **8–10KB**，撞 `WRITE_INLINE_MAX_CHARS=8192`（[BUG-024](./2026-08-05-inline-write-repeat-guard-loop.md) 症状；本 bug 为**上游根因**）。
5. `read_file` 回灌的 `content` 已含增殖 `\r`；再 `write_text` **每轮重写 `\r` 再翻倍**。

与 BUG-024 **可同会话叠加**：024 止 guard 连刷；**025 修文件如何被写坏**。

### 1.1 会话量化（`20260804-dcef2d2b` · 2026-08-05）

| 指标 | 值 |
|------|-----|
| 助手「乱了 / 重写」类消息 | **12** |
| `views/*.vue` · `patch_file` find 模式 | **63** |
| `views/*.vue` · `patch_file` line_range | **34** |
| `views/*.vue` · `write_text` 成功 | **22** |
| 当日 `inline_write_max` guard | **6**（同会话） |

---

## 2. 根因

### 2.1 P0 — `patch_file` find 模式在 CRLF 文件上增殖 `\r`

**读**：`text = raw.decode("utf-8")` — 保留原始 `\r\n`。  
**写**：`target.write_text(new_text, encoding="utf-8")` — 默认 `newline=None`，Python 在 Windows 上将每个 `\n` 译为 `os.linesep`（`\r\n`）。

对已是 `\r\n` 的文本，**每个换行前的 `\n` 会再插入一个 `\r`**：

```text
初始:           line1\r\nline2\r\n
1 次 find patch: LINE1\r\r\nline2\r\r\n
2 次 find patch: LINE1\r\r\r\nLINE2\r\r\r\n
N 次后:         …\r\r\r\r\r\n…   （与 huiyi 磁盘实测一致）
```

**代码锚点**：`evolve/tools/coding/patch_file/main.py`

- find 分支：`target.write_text(new_text, encoding="utf-8")`（约 L147）
- line_range 分支：`_write_text_lines` 使用 `newline=""` — **不受此 bug 影响**

对比：line_range 用 `open(..., newline="")` + `writelines` — 不增殖 `\r`。

### 2.2 P0 — `read_file` → `write_text` 放大

**读**：`read_file` → `raw.decode("utf-8")`，**不**规范化换行（`agent-core/tools/builtin/read_file.py`）。  
**写**：`write_text` → `target.write_text(content, encoding="utf-8")`（`evolve/tools/common/write_text/main.py` L80）。

助手从 tool 结果复制 `content` 再整文件写入时，**每轮 `\r` 翻倍**（与 2.1 同机制）。

### 2.3 P1 — `patch_file` line_range 结构性破坏

- 模型按 `read_file` 的 **逻辑行号**（`\n` 计数）调用 `start_line`/`end_line`；磁盘行可能已因 `\r` 增殖与 **readlines 物理行** 错位。
- 典型坏例（evolve_log `2026-08-05T02:47:17`）：`start_line: 8, end_line: 8`，用含新 `<el-table>` + `#empty` 的**多行 replacement** 替换单行 opening tag，**未保留列定义与闭合标签** → 模板损坏。
- 批量并行 patch 8 个页面时更易触发。

### 2.4 与 BUG-024 的关系

| 层 | BUG-025 | BUG-024 |
|----|---------|---------|
| 文件内容 | **写坏 / `\r` 增殖 / 结构乱** | 不直接改内容 |
| 恢复手段 | 助手倾向 inline 整文件 `write_text` | 8192 guard 拒 inline |
| 用户体感 | 「文件又乱了」 | guard 连刷 · 不停 tool |
| 修复 | 工具换行语义 | streak ≥2 停 tool（**已 fixed**） |

---

## 3. 已决修复（待实施）

### 3.1 规则 **R1** — `patch_file` find 写盘与 line_range 对齐

| 项 | 值 |
|----|-----|
| 写盘 | find 模式改用 `open("w", encoding="utf-8", newline="")` + `write`，**或**写前 `normalize_newlines` |
| 策略 | 推荐 **统一 LF 内存表示 + `newline=""` 落盘**（与 line_range 一致） |
| 非目标 | 不改 find 匹配语义（仍对解码后 `str` 操作） |

### 3.2 规则 **R2** — `write_text` 写前规范化

| 项 | 值 |
|----|-----|
| 时机 | `run_write` 落盘前 |
| 规则 | `content.replace("\r\n", "\n").replace("\r", "\n")` 再 `open(..., newline="\n")` 或 `newline=""` + 统一 `\n` |
| 目标 | 打断 read→write `\r` 放大链 |

### 3.3 规则 **R3** — `read_file` 可选规范化（M1）

| 项 | 值 |
|----|-----|
| 默认 | 保持 raw decode（兼容依赖 `\r` 的测试） |
| 可选 | `normalize_newlines: true` 参数，或项目模式默认规范化（需 IT 覆盖） |
| M0 | **仅 R1+R2** 即可打断主路径 |

### 3.4 规则 **R4** — 提示词 / harness（M1）

| 场景 | 约束 |
|------|------|
| Vue/多行源文件 >6KB | 禁止 inline `content`；`_staging` + `content_workspace_path` |
| 插多行块（empty slot、skeleton） | **禁止** `start_line`+`end_line` 单行替换多行；用 **find 唯一锚点** 或 staging 整文件 |
| 批量改 N 个同构页面 | 先改 1 个验证 build，再复制模式（降并行 patch 风险） |

### 3.5 规则 **R5** — 一次性清理（手工 / S-99）

| 项 | 值 |
|----|-----|
| 范围 | `workspace/huiyi/frontend/src/views/*.vue`（及已污染其它文本） |
| 动作 | normalize 为 `\n` 或统一 `\r\n`（与 R1/R2 策略一致） |
| 验收 | `npm run build` 通过；`doubleCR` 计数为 0 |

### 3.6 非目标

- 取消 `WRITE_INLINE_MAX_CHARS=8192`。
- 废除 `patch_file` line_range（保留，靠 R4 约束用法）。
- 在 M0 引入 Cursor 式 IDE patch（长期方向）。

---

## 4. 落点

| 文件 | 改动 |
|------|------|
| `agent-core/evolve_tool_io.py` | `normalize_newlines` · `write_utf8_text` |
| `evolve/tools/coding/patch_file/main.py` | R1：find + line_range 经 `write_utf8_text` |
| `evolve/tools/common/write_text/main.py` | R2：`write_utf8_text` 落盘 |
| `agent-core/tools/builtin/read_file.py` | R3（M1）：可选 normalize |
| `agent-core/prompts/core.txt` | R4：大文件 staging · patch find 优先 |
| `evolve/prompts/coding.md` · `evolve/prompts/project.md` | R4：coding/项目主题写盘纪律 |
| `evolve/tool-catalog/buckets/write.md` | R4：write bucket 工具目录 |
| `tests/test_patch_file_crlf.py`（新） | **IT-99** |
| `docs/TOOLS.md` | `patch_file` / `write_text` 换行约定 |

**重置 / 迁移**：已污染文件靠 R5 手工 normalize；修工具后**新写入**不再增殖。

---

## 5. 验收

| ID | 场景 | 通过标准 |
|----|------|----------|
| **IT-99a** | CRLF 文件上连续 5 次 find `patch_file` | 字节中无 `\r\r`；行尾仍为 `\r\n` 或统一 `\n`（与 R1 策略一致） |
| **IT-99b** | `read_file` 读污染模拟内容 → `write_text` 同 content 写回 | `\r` 计数不增加 |
| **IT-99c** | line_range patch 回归 | 现有 demo 仍绿 |
| **S-99** | huiyi `views/*.vue` normalize + `npm run build` | 构建通过；助手不再同日 10+「乱了」（观察） |

---

## 6. 临时规避（实施前）

1. **少用手动 find patch 链**：同一 CRLF 文件 find patch **≤2 次**后改 staging 整文件或 Cursor 原生编辑。
2. 跟助手明示：「大 Vue 只用 `_staging + content_workspace_path`；不要 `start_line` 插多行块。」
3. 大改前 **git commit**；乱了用 diff 回滚，勿让助手连刷 `write_text`。
4. 重启 desktop 使 **BUG-024** 生效，减轻 8192 guard 连刷（不治本）。

---

## 7. 工作留痕

| 日期 | 事项 |
|------|------|
| 2026-08-05 | 用户反馈：同日 10+ 次「文件又乱了」；要求查 my-agent 侧 |
| 2026-08-05 | 复现：`patch_file` find + `write_text` 在 Windows CRLF 上 `\r` 增殖；关联 BUG-024 |
| 2026-08-05 | **T-4252～4254 done**：`evolve_tool_io.write_utf8_text` · `patch_file` · `write_text` · `tests/test_patch_file_crlf.py` · IT-99 绿 |
| 2026-08-05 | **T-4255 done**：`core.txt` · `coding.md` · `project.md` · `buckets/write.md` 写盘纪律 |
