# 项目开发工具补齐（PROJECT-DEV-TOOLS）

> 版本 **0.3.0** · 2026-08-01 · **状态：M0+M1 实现**（D1～D4 已决）  
> Phase **26** · 关联：[RUN-SERVICE.md](./RUN-SERVICE.md) · [TOOL-CATALOG.md](./TOOL-CATALOG.md) · [PROGRESS-GATE.md](./PROGRESS-GATE.md) · [GIT-VENDOR.md](./GIT-VENDOR.md)

## 0. 为什么开这个 Phase

写项目（典型：`workspace/<id>/` 下 Spring + npm）主路径已有：

| 能力 | 工具 |
|------|------|
| 读写改 / 补丁 | builtin + `write_text` / `append_text` / `patch_file` / `copy_move` |
| 构建与一次性命令 | `mvn_exec` / `npm_exec` / `run_python` / `repl` |
| 长驻进程 | `run_service`（Phase 25） |
| 进度 | `report_progress` + Progress Gate |
| Git 只读 / 克隆 | `git_snapshot` / `git_clone` |

会话盘点后，**仍会卡住**的缺口集中在：起服后的 **HTTP 探活与调 API**、端口占用治理、Git **写侧收尾**、以及磁盘上已出现的 **`dev_start` 与 `run_service` 重叠**。

本 Phase **只定契约与优先级，不在本文落地代码**。实现须另开任务并过 IT。

---

## 1. 已有面 vs 缺口（盘点）

### 1.1 已有（不重做）

- lint / format / test → 继续走 `npm_exec` / `mvn_exec`
- 装前端依赖 → `npm_exec`
- 网页检索 → builtin `web_search` / `fetch_url`
- 主机目录 → host 工具 archived；走桌面托管设置

### 1.2 缺口（按优先级）

| P | 缺口 | 痛点 | 拟议交付 | 建议里程碑 |
|---|------|------|----------|------------|
| **P0** | HTTP 探活 / 调 API | 端口通 ≠ 应用就绪；无法验 REST | `http_request`（evolved） | **M0** |
| **P0** | `dev_start` vs `run_service` | 两套 active 启停，模型易混用 | **收敛**：`dev_start` → archived 或薄封装调 `run_service` | **M0** |
| **P1** | 按端口查/杀进程 | 8080 被占、非本工具登记的残留 | `run_service` 增 `port_status` / `kill_port` **或** 独立小工具 | **M1** |
| **P1** | Git 写侧（受控 commit） | 做完无法收尾；仅 snapshot | `git_commit`（confirm；禁 force / 禁改 config） | **M1** |
| **P2** | DB 速查 | Gate 有 `verify_db`，工具弱 | `db_query`（只读默认；路径/DSN 受限） | **M2** |
| **P2** | `pip_install` 出 suspect | Python 项目别扭 | 评审后 active 或文档声明替代 | **M2** |
| **defer** | 裸 shell / 多语言构建器 / 浏览器自动化 | 扩大攻击面与超时模型 | 不做 | — |

---

## 2. 已决 / 待决

### 2.1 已决（文档阶段）

1. **先文档后实现**；TASKS / MAP 留痕；缺 DOC-04 不写代码。
2. **不**把长驻超时塞进 `mvn_exec` / `npm_exec`（仍由 `run_service` 承担）。
3. **不**引入无边界的通用 shell。
4. HTTP 工具默认 **localhost / 显式 URL**；禁止无 confirm 的任意外网写（见 §3.1）。
5. Git 写侧若做：**必须 confirm**；**禁止** `--force`、改 `git config`、交互 rebase。

### 2.2 待决 → **已决**（2026-08-01 用户：「可以」采纳默认提案）

| # | 问题 | 已决 |
|---|------|------|
| D1 | M0 范围 | **A** — 只做 `http_request` + 收敛 `dev_start`（`kill_port` 留 M1） |
| D2 | 非 localhost | **B** — 允许外网，但 **always confirm**；loopback GET/HEAD 可不 confirm |
| D3 | Git 写侧 | **A** — 仅受控 `commit`（M1）；不 push / 不 force |
| D4 | `dev_start` | **B** — 保留「一键前后端」语义，**内部只调 `run_service`** |

原默认提案原文保留作审计；实现以本表为准。

---

## 3. 契约草案（实现时照此写 tool.toml）

### 3.1 `http_request`（M0）

```text
输入：method, url, headers?, body?, timeout_sec?, json?
输出：ok, status_code, headers(截断), body(截断), elapsed_ms
policy：timeout_sec ≤ 60；confirm = 对非 GET/HEAD 或非 loopback 为 true（实现时按 D2）
```

- 响应 body 硬顶（如 32KiB）+ `truncated` 标志。
- Progress Gate：成功的探活可作 **verify** 类证据（实现时改 `progress_gate` 映射，单列 IT）。

### 3.2 `dev_start` 收敛（M0）

- 现状：`evolve/tools/coding/dev_start/` 已 `status=active`（工作区曾未入库，以磁盘为准）。
- 目标：目录 INDEX / 提示词只推荐 **一条** 启停主路径（`run_service`）；`dev_start` 要么 archived，要么内部只编排 `run_service` start×2。

### 3.3 端口治理（M1）

```text
action: port_status | kill_port
port: int
```

- Windows：`netstat` / `Get-NetTCPConnection` + `taskkill /T`；Unix：`lsof`/`ss` + kill。
- `kill_port`：**always confirm**。

### 3.4 `git_commit`（M1）

```text
working_dir, message, paths?（可选暂存子集）, dry_run?
```

- 仅 `git add`（可选 paths）+ `git commit`；**无** push / force / amend（amend 另议且默认关）。
- 工作区须在 agent root 内；拒绝 `.git` 外逃逸。
- confirm = true；dry_run 预览 `status` + 将提交文件列表。

---

## 4. 非目标

- 替代用户 IDE / 手动 bat
- Docker Compose 编排、K8s
- 浏览器 E2E、截图
- 自动 bump 版本 / 发 release

---

## 5. DOC-04 准入

### 5.1 影响矩阵行（STABILIZATION §3）

| 面 | 影响 | 档位 |
|----|------|------|
| evolve 工具执行 / 清单 | 新增或收敛 coding/common 工具；INDEX `run` / 新桶行 | P0 |
| confirm 管线 | HTTP 非幂等、kill_port、git_commit 须 confirm | P0 |
| Progress Gate 证据类 | 可选：HTTP 成功 → verify 类（M0 末或 M1） | P1 |
| 桌面壳 / host / 计划门 | **无** | — |

### 5.2 回归 ID（预留；实现时落测）

| ID | 场景 |
|----|------|
| **IT-80** | `http_request` GET loopback 2xx；body 截断 |
| **IT-81** | 非 GET 或非 loopback 走 confirm；拒则不发请求 |
| **IT-82** | `dev_start` 收敛后清单/文档只暴露一条主路径；旧名不双活误导 |
| **IT-83** | `kill_port` confirm + 端口释放（M1） |
| **IT-84** | `git_commit` dry_run / 真提交 / 禁 force（M1） |

手工 smoke（可选）：**S-80** 起 `run_service` → `http_request` 探活 → 勾相关 TASK。

---

## 6. 任务拆分（见 TASKS Phase 26）

| 里程碑 | 内容 | 状态 |
|--------|------|------|
| **doc** | 本文 + MAP/TASKS | **done** |
| **D1～D4** | 用户确认 | **done**（采纳默认） |
| **M0** | `http_request` + `dev_start` 薄封装 + INDEX | **done** |
| **M1** | 端口治理 + `git_commit` | todo |
| **M2** | DB 速查 / pip 出 suspect | defer 直至 M1 完 |

---

## 7. 修订记录

| 版本 | 日期 | 说明 |
|------|------|------|
| 0.1.0 | 2026-08-01 | 初稿：盘点缺口、默认提案 D1–D4、DOC-04、IT 预留；**未实现** |
| 0.1.1 | 2026-08-01 | D1～D4 **已决**（采纳默认）；仍未实现代码 |
| 0.2.0 | 2026-08-01 | M0：`http_request` · `dev_start`→`run_service` · IT-80～82 · catalog |
