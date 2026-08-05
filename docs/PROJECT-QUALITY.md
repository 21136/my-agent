# 项目质量与数据面（PROJECT-QUALITY）

> 版本 **0.1.0** · 2026-08-04 · **状态：M0 done**（S-451 手工 todo）  
> Phase **45** · 关联：[PROJECT-DEV-TOOLS.md](./PROJECT-DEV-TOOLS.md) · [PROJECT-RECIPES.md](./PROJECT-RECIPES.md) · [PROJECT-VERIFY.md](./PROJECT-VERIFY.md) · [GOVERNANCE.md](./GOVERNANCE.md)  
> 触发：用户盘点——迁移/ lint / 部署缺口；优先级低于脚手架（43）与结构化验证（44）。

---

## 0. 一句话

**不扩十个专用工具**；用 **`ENV.md` 驱动 `run_quality`**、**只读 `db_migrate_status`**、**部署仍走 Phase 43 配方 `phase: deploy`**，lint/test 解析复用 Phase 44。

---

## 1. 缺口与策略总表

| 用户优先级 | 缺口 | 策略 | Phase |
|------------|------|------|-------|
| 🟡 3 | DB 迁移 | `db_migrate_status` 只读 + 写操作 `run_command` confirm | 45 M0 |
| 🟡 4 | Lint / 类型检查 | `ENV.md` `quality.commands` + 可选 `run_quality` | 45 M1 |
| 🟡 5 | 部署 / CI / env | [PROJECT-RECIPES.md](./PROJECT-RECIPES.md) `phase: deploy` | 43 M2 |
| — | diff 语义审查 | `git_snapshot` + checker（非新工具） | 已有 |
| — | evolve 工具审查 | `my-agent review`（T-601） | 已有 |

---

## 2. 数据库迁移（§3）

### 2.1 边界（延续 PROJECT-DEV-TOOLS §3.5）

- `db_query`：**仅 SQLite 文件只读**——不改。
- **不**在工具参数传 Postgres/MySQL DSN（密钥）。
- 迁移 **执行** → `run_command("alembic upgrade head")` / `prisma migrate deploy`（project 内已可走 A2 分层）。

### 2.2 `db_migrate_status`（evolved · 可选 M0）

```text
输入：
  working_dir: string
  backend: auto | alembic | prisma
  dry_run: bool?

输出：
  ok: bool
  backend: string
  current_revision: string | null
  heads: string[]
  pending_count: int
  dirty: bool?                 # alembic
  messages: string[]
```

| 写操作 | 工具 |
|--------|------|
| `alembic upgrade` / `revision --autogenerate` | **不**封装；`run_command` + **confirm** |
| `prisma migrate dev` | 同上 |

配方 `manifest` 可加 `kind: db_init`（`alembic init` / `prisma init`）——**初始化**，不是迁移引擎。

---

## 3. 代码质量（§4）

### 3.1 ENV.md 扩展（E11 · done）

见 [PROJECT-MODE.md](./PROJECT-MODE.md) §0c E11。

```yaml
# workspace/<id>/ENV.md
tools: { ... }                  # 已有 E1–E6
prefer: { package_manager: pnpm }

quality:
  commands:
    - id: ruff
      cmd: ["python", "-m", "ruff", "check", "."]
      cwd: backend
    - id: eslint
      cmd: ["npm", "run", "lint"]
      cwd: frontend
```

纪律：**不**每轮注入 system；`run_quality` 读盘执行。

### 3.2 `run_quality`（evolved · M1 · 可选）

```text
输入：
  working_dir: string?          # 默认 project_root
  only: string[]?               # quality.commands 的 id 子集
  fail_fast: bool?              # 默认 true

输出：
  ok: bool
  results: [{ id, ok, exit_code, violations[{file,line,message}]?, excerpt }]
```

violations 解析：**M1 仅**支持 ruff / eslint 默认格式；其余返回 excerpt。

**与 Phase 44**：violations 结构与 `run_project_tests.failures` **同形**，便于 harness 复用 spill 逻辑。

### 3.3 不做

| 项 | 理由 |
|----|------|
| 独立 `run_mypy` / `run_ruff` / `run_eslint` | PROJECT-DEV-TOOLS §1.1 已决：走 exec |
| 项目级 `my-agent review` | 治理面已覆盖 evolve；项目 code review = LLM + `git_snapshot` |

---

## 4. 部署与环境变量（§5）

| 交付物 | 路径 |
|--------|------|
| Dockerfile / docker-compose | `evolve/scaffolds/<recipe>/deploy/` |
| GitHub Actions | 同上 `*.yml.tpl` |
| `.env.example` | 模板生成；**真实 `.env`** 走 write_policy 敏感路径 confirm |

**不**新增 `generate_dockerfile` 工具——配方渲染即可。

---

## 5. 里程碑

| 里程碑 | 内容 | 状态 |
|--------|------|------|
| **D0** | 本文 | **doc** |
| **M0** | `db_migrate_status` + ENV E11 文档 | defer |
| **M1** | `run_quality` + ruff/eslint 解析 | defer |
| **M2** | 部署模板随 Phase 43 M2 | → 43 |

**推荐顺序**：**43 M0 → 44 M0 → 45 M0**（仅当项目里真碰迁移）。

---

## 6. DOC-04

| 面 | 档位 |
|----|------|
| `db_query` 邻域 | P1 |
| ENV.md schema | P2 |
| Progress Gate | `verify_db` 不变；迁移 status **不**当勾选证据 |

| ID | 场景 |
|----|------|
| **IT-451** | alembic 仓库 `db_migrate_status` → current + pending |
| **IT-452** | `run_quality` ruff 失败 → violations |
| **S-451** | 人工：配方 deploy 生成 Dockerfile 可 build |

---

## 7. 签字

- [x] D0 文档评审（2026-08-04 · 用户「文档先行」）
- [x] M0 实现（2026-08-04 · `project_quality.py` · `db_migrate_status` · `run_quality`）
