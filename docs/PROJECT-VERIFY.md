# 项目结构化验证（PROJECT-VERIFY）

> 版本 **0.1.0** · 2026-08-04 · **状态：M0+M1 done**（T-4408 S-441 手工 todo）  
> Phase **44** · 关联：[PROGRESS-GATE.md](./PROGRESS-GATE.md) · [AGENT-HARNESS.md](./AGENT-HARNESS.md) · [EXEC-RELIABILITY.md](./EXEC-RELIABILITY.md) · [CHECKER-SUBAGENT.md](./CHECKER-SUBAGENT.md) · [PROJECT-DEV-TOOLS.md](./PROJECT-DEV-TOOLS.md)  
> 触发：用户盘点——`run_tests` 只跑 my-agent 自身验收；项目测试失败需人工读日志找行号；缺「跑测→结构化失败→patch→重测」桥接。

---

## 0. 一句话

**不做黑盒 auto-patch 闭环**；新增 **`run_project_tests`** 把 pytest/jest/surefire 输出解析成 **`{file, line, message}[]`**，喂给 harness + LLM，由 **`patch_file`** 与 **Progress Gate `test` 证据** 完成可审修复环。

---

## 1. 动机

### 1.1 碎片化现状

```text
mvn_exec test  →  64KiB 日志进 history（或 failure spill 摘要仍缺行号）
       ↓
用户 / 助手肉眼找 Failed tests
       ↓
口述「改第 42 行」→ patch_file
       ↓
再 mvn_exec  →  重复
```

| 组件 | 问题 |
|------|------|
| `run_tests` | 只跑 **agent-core + evolve** 自带 demo，非用户项目 |
| `mvn_exec` / `npm_exec` / `run_command` | 成功/失败二元；无统一 **violations** 结构 |
| Progress Gate | `test` 证据认 `mvn_exec` 等 **ok**，不认「哪些用例挂了」 |
| EXEC-RELIABILITY | **明确废止**剧本自动修复；**不**引入 OpenHands 式封闭环 |
| Phase 22 `auto_fix` | 仅 **TASKS.md** 去重，不修代码 |

### 1.2 目标态

```text
run_project_tests(working_dir, suite)
    → { ok, summary, failures[{file,line,col,test,message}] }
    → agent failure spill（结构化摘要进 LLM）
    → LLM: read_file + patch_file
    → run_project_tests（同 suite）
    → report_progress（test 证据 · 本回合 ok）
```

**环由 LLM 驱动**；段内失败预算（P5）、confirm（patch）、用户可见 diff **保留**。

---

## 2. 设计原则

| ID | 决议 |
|----|------|
| **V0** | **结构化输出** > 再包十个 test 工具 |
| **V1** | **不**在内核实现「失败自动 patch 直到绿」 |
| **V2** | 解析器 **确定性**（regex / JUnit XML）；解析失败仍返回 `raw_excerpt` |
| **V3** | 与 Progress Gate：**本回合** `run_project_tests` **ok** → `test` 证据 |
| **V4** | 与 `run_tests`（my-agent 自有）**名称区分**；INDEX 写清 |
| **V5** | 可选 **checker** kind `project_test_fail`：只读分析，**禁止**自动 patch |

---

## 3. `run_project_tests` 工具契约（evolved · M0）

```text
输入：
  working_dir: string           # 相对 agent root；须在 project_root 下
  suite: auto | pytest | jest | vitest | mvn | npm_test
  extra_args: string[]?        # 追加 CLI 参数（受限字符集）
  timeout_sec: int?             # 默认 600；上限 1800
  max_failures: int?            # 解析后最多返回 N 条；默认 20
  dry_run: bool?

输出：
  ok: bool                      # 进程 exit 0 且解析完成
  suite: string
  command: string              # 实际执行的命令（可审）
  exit_code: int
  duration_ms: int
  summary: { passed?, failed?, skipped?, total? }
  failures: [
    { file, line?, col?, test?, message, raw? }
  ]
  raw_excerpt: string           # 截断原文；解析失败时仍返回
  parse_ok: bool
```

| policy | 值 |
|--------|-----|
| `confirm` | **false**（只读跑测；对齐 project 内 `run_command` test 类） |
| `scope` | `project` |

### 3.1 `suite` 检测与命令（默认）

| suite | 检测启发式 | 默认命令 |
|-------|------------|----------|
| `auto` | `pom.xml` → mvn；`package.json` scripts.test → npm；`pytest.ini`/`pyproject.toml` → pytest | 首匹配 |
| `pytest` | — | `python -m pytest -q --tb=short` |
| `jest` | — | `npm test -- --ci`（经 `npm_exec` + ENV） |
| `vitest` | — | `npm run test -- --run` |
| `mvn` | — | `mvn -q test` |
| `npm_test` | — | `npm test` |

实现应 **复用** `npm_exec` / `mvn_exec` / `run_command` 内部执行器，避免第三套 subprocess。

### 3.2 解析器（M0 范围）

| 格式 | 来源 | 提取 |
|------|------|------|
| pytest | `FAILED path::test` + short tb | file, line, test, message |
| Jest/Vitest | 默认 reporter 单行 `at file:line:col` | file, line, col, message |
| Maven Surefire | `target/surefire-reports/*.txt` 优先；fallback stdout | class, method, message |
| 解析失败 | — | `parse_ok:false` + `raw_excerpt` |

**M1**：JUnit XML（`--junitxml`）统一路径。

---

## 4. Harness 对接（AGENT-HARNESS P4 对称）

| 项 | 行为 |
|----|------|
| 失败回灌 | `failures[]` 优先注入 LLM；`raw_excerpt` 限长（如 4KiB） |
| 成功 | `summary` 一行；不全文 spill |
| 段内失败预算 | 连续 `run_project_tests` 失败计 countable（与 P5 一致） |
| 熔断 | 同 `suite`+`working_dir` 指纹连败 ≥3 → 停段提示换招 |

**不改** `core.txt`；停段用 `[内核]` 注入（§2.1 F 层纪律）。

---

## 5. Progress Gate 对接

在 `progress_gate.py`：

```python
_TEST_EVIDENCE_TOOLS  # 增加 "run_project_tests"
```

| 条件 | `report_progress` |
|------|-------------------|
| `evidence_kind=test` + 本回合 `run_project_tests` **ok:true** | 允许勾选 |
| `ok:false` 但 `mvn_exec` ok | **仍拒**（对口工具须 test 类且成功） |
| `failures` 非空 | `ok` 必 false |

侧栏可审产物（G6）：`partner_notices` 展示 `failures[0..2]` 摘要 + 「已跑 suite / command」。

---

## 6. Checker 子代理（可选 · M1）

| 项 | 值 |
|----|-----|
| `kind` | `project_test_fail` |
| 触发 | 用户 `验收测试` / 或 `run_project_tests` 失败后用户确认 |
| 输入 | `failures[]` + 相关 `read_file` 切片 |
| 输出 | 修复建议（**不**调 `patch_file`） |
| 与 grow `evolve_tool_scaffold` | **独立** kind（CHECKER §227 远期交付检查） |

---

## 7. 与 `run_tests` 区分

| 工具 | 对象 | 用途 |
|------|------|------|
| `run_tests` | my-agent 仓库 | 进化/治理回归；coding 主题 |
| `run_project_tests` | 绑定 `project_root` | 用户项目 pytest/jest/mvn |

INDEX / `project.md` bucket **必须**并列说明，防混调。

---

## 8. 非目标（本 Phase）

| 非目标 | 理由 |
|--------|------|
| `auto_fix_loop` 工具（直到绿） | V1；与 confirm / 失败预算冲突 |
| 替代 `run_tests` | 职责不同 |
| E2E / Playwright | PROJECT-DEV-TOOLS defer |
| 覆盖率报告 / 变异测试 | 单人场景 ROI 低 |
| LLM 解析日志 | V2 要求确定性解析 |

---

## 9. 里程碑

| 里程碑 | 内容 | 状态 |
|--------|------|------|
| **D0** | 本文 + TASKS/MAP | **doc** |
| **M0** | `run_project_tests` + pytest + mvn 解析 + Gate | **done** |
| **M1** | jest/vitest + checker kind + failure spill | **done** |
| **M2** | `suite:auto` + JUnit XML | todo |

---

## 10. DOC-04

### 10.1 影响矩阵

| 面 | 影响 | 档位 |
|----|------|------|
| evolved registry | `run_project_tests` | P1 |
| `progress_gate.py` | `_TEST_EVIDENCE_TOOLS` | P0 |
| agent failure spill | 结构化 failures | P1 |
| exec_reliability 熔断 | 新指纹 | P1 回归 |
| subagent | 可选 `project_test_fail` | P2 |
| INDEX / project bucket | 与 `run_tests` 区分 | P1 |

### 10.2 回归 ID

| ID | 场景 |
|----|------|
| **IT-441** | pytest 失败 → `failures[0].file` + line 存在 |
| **IT-442** | mvn surefire 失败 → 至少 1 failure |
| **IT-443** | 本回合 `run_project_tests` ok → Gate 允许 `test` 勾选 |
| **IT-444** | 本回合失败 → Gate 拒勾 |
| **IT-445** | 解析失败 → `parse_ok:false` + `raw_excerpt` |
| **S-441** | 人工：失败→patch→重测→report_progress 一轮通 |

---

## 11. 开放问题 → 默认

| # | 问题 | 默认 |
|---|------|------|
| Q1 | 是否合并进 `mvn_exec` 的 `mode:test`？ | **否**；独立工具保持解析契约 |
| Q2 | `extra_args` 允许 `--` 吗？ | 允许；禁 shell 元字符 |
| Q3 | 失败时 spawn checker 自动？ | **否**；M1 仅手动 / 用户触发 |

---

## 12. 签字

- [x] D0 文档评审（2026-08-04 · 用户「文档先行」）
- [x] M0+M1 实现（2026-08-04 · `project_verify.py` · checker `project_test_fail`）
