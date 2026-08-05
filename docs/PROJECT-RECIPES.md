# 项目配方脚手架（PROJECT-RECIPES）

> 版本 **0.1.0** · 2026-08-04 · **状态：M0+M1+M2 done**  
> Phase **43** · 关联：[PROJECT-MODE.md](./PROJECT-MODE.md) §0c · [PROJECT-DEV-TOOLS.md](./PROJECT-DEV-TOOLS.md) · [PROGRESS-GATE.md](./PROGRESS-GATE.md) · [EXEC-RELIABILITY.md](./EXEC-RELIABILITY.md) · [TOOLS.md](./TOOLS.md)  
> 触发：用户盘点——从零建项目无配方；`scaffold_demo` 仅为 `write_evolve` 演示目录，非项目脚手架。

---

## 0. 一句话

**不让 LLM 自由搭整仓**；用 **`evolve/scaffolds/<recipe>/` 静态配方** + 薄工具 **`scaffold_project`** 编排 `run_command` / `write_text` / `npm_exec`，并与 **`create_project`** / **`ENV.md`** 挂钩。

---

## 1. 动机

### 1.1 现状（能跑，缺配方）

| 已有 | 缺口 |
|------|------|
| `create_project` + `workspace/<id>/` | 无「FastAPI+Vue / Spring+Vue」等**可重复**初始化 |
| `run_command` / `npm_exec` / `mvn_exec` | 模型需自己拼 `npm create vite`、装依赖、初始化 DB |
| `write_text` / `patch_file` | 无模板包；复杂骨架易漂移 |
| E2 `ENV.md` 探测 node/mvn/java | 只探测本机工具，**不生成**项目结构 |
| `scaffold_demo`（`agent.py` demo） | **误导名**——测 `write_evolve` 后删除；与项目脚手架无关 |

### 1.2 与废止债的关系

| 条目 | 关系 |
|------|------|
| **T-906 自动 venv**（wontfix） | **不**在内核偷偷建 venv；配方步骤里 **显式** `python -m venv` + **confirm** 可接受 |
| **不新增十个 `*_exec`** | 配方 **内部** 调现有 `run_command` / `npm_exec`；只新增 **一个** 编排工具 `scaffold_project` |
| **EXEC-RELIABILITY** | 脚手架完成 ≠ 「页面可用」；起服仍须 `run_service` ready+alive |

---

## 2. 设计原则

| ID | 决议 |
|----|------|
| **R0** | **配方优先于即兴**：步骤来自 `manifest.yaml`，LLM 只选 `recipe` + 填变量 |
| **R1** | **模板 + 占位符**，禁止从零生成复杂多文件树（对齐 docx 母版教训） |
| **R2** | 第一版 **2～3 个配方**，覆盖真实高频栈；不全覆盖生态 |
| **R3** | 每步返回 **结构化** `{step_id, ok, log_excerpt}`；失败即停，不盲跳 |
| **R4** | 与 `create_project` **可选挂钩**：`template: <recipe>` 创建后自动跑 `phase: init` |
| **R5** | **部署**（Dockerfile / CI / `.env.example`）作为配方的 **`phase: deploy`**，不另开十工具 |
| **R6** | 路径须在 **project_root** 或新建 workspace 内；WRITE-SCOPE / confirm 仍由 executor 强制 |

---

## 3. 目录布局

```text
evolve/scaffolds/
├── README.md                    # 配方索引（给人 + LLM read_file）
├── spring-vue/
│   ├── manifest.yaml            # 步骤、变量、验收
│   ├── templates/               # 带 {{var}} 的静态文件
│   │   ├── backend/pom.xml.tpl
│   │   └── frontend/package.json.tpl
│   └── deploy/                  # phase: deploy 专用（可选）
│       ├── Dockerfile.backend.tpl
│       └── .github/workflows/ci.yml.tpl
└── fastapi-vue/
    ├── manifest.yaml
    └── templates/
        └── ...
```

**不进** `evolve/tools/`——配方是 **数据 + 模板**，不是 evolved 工具实现。

---

## 4. `manifest.yaml` 契约（草案）

```yaml
id: spring-vue
version: "1.0.0"
description: "Spring Boot + Vue 3 单体仓库骨架"
variables:
  project_name: { required: true, pattern: "^[a-z][a-z0-9-]*$" }
  java_version: { default: "17" }
  package_manager: { default: "pnpm", enum: [npm, pnpm, yarn] }

phases:
  init:
    steps:
      - id: mkdir_layout
        kind: template_tree          # 渲染 templates/ → target_dir
        target: "."
      - id: mvn_wrapper
        kind: run_command
        command: "mvn -N wrapper:wrapper"
        working_dir: backend
        confirm: false               # project 内 build 类；executor 仍裁决
      - id: npm_install
        kind: npm_exec
        subcommand: install
        working_dir: frontend
      - id: verify_compile
        kind: mvn_exec
        goals: ["-q", "compile"]
        working_dir: backend
        evidence: compile            # 供 Progress Gate 对口（可选元数据）

  deploy:
    steps:
      - id: dockerfile
        kind: template_tree
        source: deploy/
        target: "."
      - id: env_example
        kind: template_file
        source: deploy/.env.example.tpl
        target: .env.example
```

### 4.1 `step.kind` 枚举（M0）

| kind | 行为 | 底层 |
|------|------|------|
| `template_tree` | 渲染目录下 `*.tpl` → 去 `.tpl` | `write_text`（批量；project 内免确认走 H 轨） |
| `template_file` | 单文件渲染 | `write_text` |
| `run_command` | 透传 command + working_dir | `run_command` / executor |
| `npm_exec` | subcommand + working_dir | `npm_exec` |
| `mvn_exec` | goals + working_dir | `mvn_exec` |
| `write_env_md` | 合并配方默认进 `ENV.md` | `project_env` 钩子 |

**M1+**：`kind: db_init`（只调 `alembic init` / `prisma init` 命令，不内置迁移引擎）——见 [PROJECT-QUALITY.md](./PROJECT-QUALITY.md)。

---

## 5. `scaffold_project` 工具契约（evolved · M0）

```text
输入：
  recipe: string              # manifest id，如 spring-vue
  target_dir: string          # 相对 agent root；通常 workspace/<id>/
  phase: init | deploy        # 默认 init
  variables: object?          # 覆盖 manifest.variables
  dry_run: bool?              # 只列将执行的步骤
  stop_on_error: bool?        # 默认 true

输出：
  ok: bool
  recipe, phase, steps_run: [{id, ok, elapsed_ms, log_excerpt}]
  failed_step: string | null
  evidence_hints: string[]    # 如 ["compile", "build_fe"] 供 Gate 提示
```

| policy | 值 |
|--------|-----|
| `confirm` | **true**（整次脚手架）；`dry_run` 不 confirm |
| `scope` | `project`（绑定 project 时并入清单，对齐 `report_progress`） |
| `topics` | `["coding", "project"]` |

**执行器**：逐步执行；单步失败且 `stop_on_error` → 返回 `ok:false` + `failed_step`；**不**自动回滚已写文件（用户可 `git` / 删目录）。

---

## 6. 与 `create_project` 集成

| 入口 | 行为 |
|------|------|
| WS `project.create` + `template: spring-vue` | `create_project` → 写三件套 + ENV.md → 调 `scaffold_project(phase=init)` |
| 已有项目 | 仅 `scaffold_project`，`target_dir` = 当前 `project_root` |
| CLI（可选 M1） | `my-agent scaffold --recipe fastapi-vue --id demo` |

挂钩点：`agent-core/project_mode.py` · `context_switch.create_project_with_session_isolation` · `project_env.py`。

---

## 7. 第一版配方（M0 目标）

| recipe | 栈 | 验收步骤 | 优先级 |
|--------|-----|----------|--------|
| **spring-vue** | Spring Boot + Vue3 + Maven + pnpm | `mvn compile` + `pnpm run build` | **P0**（huiyi 类） |
| **fastapi-vue** | FastAPI + Vue3 + venv + pip | `pytest -q`（空测试通过）+ `pnpm build` | **P1** |
| **next-prisma** | Next.js + Prisma + SQLite | `prisma db push` + `next build` | **defer**（M1 候选） |

每个配方须带 **`evolve/scaffolds/<id>/README.md`**：变量说明、手动验收命令、已知限制。

---

## 8. Progress Gate 对接

| 配方末步 `evidence` 元数据 | Gate `evidence_kind` |
|----------------------------|----------------------|
| `compile` | `compile` |
| `build_fe` | `build_fe` |
| `test` | `test` |
| `verify_db` | `verify_db` |

`scaffold_project` **成功**且末步带 `evidence` → 本回合可对口 `report_progress`（实现时在 `progress_gate.py` 将 `scaffold_project` 纳入对口表，或末步显式调 `mvn_exec`/`npm_exec` 已满足则不必改表）。

**开放（Q1）**：是否把 `scaffold_project` 本身加入 `_COMPILE_EVIDENCE_TOOLS`？**默认：否**——末步应对口真实 build/test 工具成功。

---

## 9. 非目标（本 Phase）

| 非目标 | 理由 |
|--------|------|
| LLM 无配方自由生成整仓 | R1；偏离可审产物 |
| 万能 `npm create` 包装器 | 无验收标准；配方外一律 `run_command` |
| 内核自动 venv（T-906） | 废止债；配方内显式步骤可 confirm |
| 迁移文件生成 / 多 DB | → [PROJECT-QUALITY.md](./PROJECT-QUALITY.md) Phase 45 |
| 封闭「脚手架→起服→勾 TASKS」一键 | 仍须 Progress Gate + 用户 confirm |
| 重命名历史 `scaffold_demo` | 代码 demo 已自删；文档澄清即可（§1.1） |

---

## 10. 里程碑

| 里程碑 | 内容 | 状态 |
|--------|------|------|
| **D0** | 本文 + TASKS/MAP 挂钩 | **doc** |
| **M0** | `scaffold_project` + **spring-vue** 配方 + 单测 | **done** |
| **M1** | `create_project` template 挂钩 + **fastapi-vue** | **done** |
| **M2** | `phase: deploy` + spring-vue deploy 模板 | **done** |

---

## 11. DOC-04

### 11.1 影响矩阵

| 面 | 影响 | 档位 |
|----|------|------|
| evolved 工具 / registry | 新 `scaffold_project` | P1 |
| `create_project` / WS | 可选 `template` | P1 |
| confirm 管线 | 整次脚手架 confirm | P1 |
| WRITE-SCOPE / write_policy | 模板批量写 project 内 | P0 回归 |
| Progress Gate | 末步 evidence 元数据 | P1 |
| `project_env` / ENV.md | `write_env_md` 步骤 | P2 |

### 11.2 回归 ID

| ID | 场景 |
|----|------|
| **IT-431** | `dry_run` 列出步骤、不写盘 |
| **IT-432** | spring-vue init 在临时 workspace → `mvn compile` 步 ok |
| **IT-433** | 未知 recipe → 结构化错误 |
| **IT-434** | 中途失败 → `failed_step` + 已写文件列表 |
| **IT-435** | `create_project` + `template` 挂钩端到端 |
| **S-431** | 人工：桌面新建项目选配方 → 目录树 + ENV.md 可读 |

---

## 12. 开放问题 → 默认提案

| # | 问题 | 默认 |
|---|------|------|
| Q1 | Gate 是否认 `scaffold_project` 本身？ | **否**；认末步 build/test |
| Q2 | 失败是否自动 `git clean`？ | **否** |
| Q3 | 配方版本升级？ | manifest `version`；不自动改已有项目 |
| Q4 | 模板引擎？ | **简单 `{{var}}` 替换**；M0 不用 Jinja |

---

## 13. 签字

- [x] D0 文档评审（2026-08-04 · 用户「文档先行」）
- [x] M0～M2 实现（2026-08-04 · `scaffold_recipes.py` · spring-vue · fastapi-vue · deploy）
