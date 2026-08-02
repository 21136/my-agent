# 执行可观测（EXEC-OBSERVABILITY）

> 版本 **0.1.0** · 2026-08-02 · **状态：doc（未实现）**  
> Phase **27** · 关联：[RUN-SERVICE.md](./RUN-SERVICE.md) · [PROJECT-DEV-TOOLS.md](./PROJECT-DEV-TOOLS.md) · [PROGRESS-GATE.md](./PROGRESS-GATE.md) · [PROJECT-SIDEBAR.md](./PROJECT-SIDEBAR.md) · [CONFIRM-PIPELINE.md](./CONFIRM-PIPELINE.md) · [UX-POLISH.md](./UX-POLISH.md)

## 0. 为什么开这个 Phase

huiyi 联调中用户确认 `mvn_exec spring-boot:run` 后，聊天只剩「已执行」，**看不到启动过程**；同时侧栏只有 TASK 勾选，**看不到服务是否活着、本回合证据、失败原因**。  
结果是黑盒：难以判断是工具选错、门禁、超时，还是项目代码挂了。

产品选择（用户 2026-08-02）：**聊天过程 + 侧栏服务/进度看板都要**——可观测性是主能力，不是装饰。

---

## 1. 目标与非目标

### 1.1 目标

1. **聊天**：每个工具调用从确认 → 运行中 → 结束，状态可读；长任务可见耗时与日志尾；失败高亮原因。
2. **侧栏（project 视角）**：服务面板（`run_service` 登记项）+ 本回合证据/武装任务一眼对齐。
3. **不改工具语义**：长驻仍走 `run_service`；禁止靠拉长 `mvn_exec` 超时冒充「看得见」。

### 1.2 非目标（本 Phase）

- 完整嵌入式终端 / xterm 仿真
- 把 reasoning 全文默认展开（可折叠即可）
- 远程遥测、多机编排
- 重做确认管线协议（只增强呈现与增量事件）

---

## 2. 现状骨架（已有）

| 能力 | 现状 | 缺口 |
|------|------|------|
| `tool.start` / `tool.end` | 有；unified `showProcess: true` | process 块只有一行摘要，无日志尾、无「运行中」卡片 |
| 确认卡 | 有；同意 → 文案「已执行」 | 未过渡到运行中 UI；像「已经完事了」 |
| `run_service` 日志文件 | `data/services/<name>.log` | 桌面不订阅、侧栏无服务列表 |
| Progress Gate 证据 | 内核有 `turn_evidence` | 侧栏不展示本回合证据 |
| 状态栏 | `· run_evolved` / 就绪 | 太弱，不承载排障 |

---

## 3. 信息架构（两面一体）

```text
确认同意
  → 聊天：工具运行卡（running）
  → （若 run_service start）侧栏服务行：starting → running/failed
  → 增量：tool.progress / log.tail（可选）
  → tool.end：成功摘要或失败原因 + 「展开日志」
侧栏常驻
  → Services：list + 端口 + 打开日志尾
  → Turn：armed 任务 + 本回合证据条目（只读）
```

---

## 4. 契约草案

### 4.1 聊天 · 工具运行卡（M0）

确认 `y` / `a` 后：

1. 确认卡标记已同意，**立刻**出现或升级为 **RunningCard**：
   - 标题：`evolved` 名或 builtin 名 + 一句 summary
   - 状态：`运行中…` + 已用时（秒表）
   - 可折叠：参数摘要（已有 preview 可复用）
2. `tool.end`：
   - ok：`完成 · 1.2s` + 短结果（path / exit_code / ready）
   - 失败：红条 + `error.message`（一行）+ 「详情」展开 `details` / 截断 stdout 尾
3. 文案：**禁止**单独用「已执行」代表成功；改为「已同意，执行中…」→「完成/失败」。

依赖事件（优先复用，缺则补）：

| 事件 | 用途 |
|------|------|
| 已有 `tool.start` / `tool.end` | 起止与 ok/summary |
| **新增（建议）** `tool.progress` | `{call_id, text?, pct?, phase?}` 可选；M0 可仅前端秒表 |
| **新增（建议）** `service.log_tail` 或复用 tool 结果 | 长驻启动时推末 N 行 |

M0 最低：即使没有 progress 事件，也要有 **RunningCard + 秒表 + end 结果**，消灭「点完就静音」。

### 4.2 聊天 · 长驻专项（M0/M1）

当 `run_evolved` · `run_service`/`dev_start` 且 action∈{start,restart}：

- RunningCard 副标题显示 `name` / port
- end 时若 `ready=false`：默认展开 `logs_tail`（截断）
- 提示文案（一次）：「长驻日志在 data/services/…；侧栏可刷新」

提示词 / catalog：继续禁止用 `mvn_exec`/`npm_exec` 跑 `*:run` / `dev`（与 Phase 25/26 一致）。

### 4.3 侧栏 · 服务面板（M0）

project 视角侧栏新增 **Services** 块：

| 字段 | 来源 |
|------|------|
| name / alive / status / port | `run_service` list + 状态 json；或 WS `services.state` |
| 操作 | 刷新；「日志」拉 tail（只读，不 confirm） |

刷新策略：

- 打开项目 / 回合结束 / 用户点刷新
- M1：sidecar 在 `run_service` start/stop 后推 `services.state`

### 4.4 侧栏 · 回合证据条（M1）

在 TASK 列表附近展示只读：

- 武装中：`T-xxx` + 文案摘要
- 本回合证据：`write_text ✓` / `mvn_exec ✗` …（来自 `turn_evidence` 或 `project.state` 扩展字段）

与 Phase 24 对齐：人只看病因，不提供「强制勾选」按钮。

---

## 5. 已决 / 待决

### 5.1 已决

1. **两面都做**：聊天过程 + 侧栏服务/进度（用户明确）。
2. 先文档后实现；DOC-04 齐全再写代码。
3. 不靠拉长 `mvn_exec` 超时解决「看不见」。
4. 失败比成功更显眼。

### 5.2 待决（动手前可快速确认，有默认）

| # | 问题 | 默认提案 |
|---|------|----------|
| D1 | M0 是否包含后端 `tool.progress` 事件？ | **A** M0 仅前端秒表 + end 摘要；progress 事件 **M1** |
| D2 | 服务面板是否一切视角都显示？ | **A** 仅 `perspective=project`；grow 只靠聊天卡 |
| D3 | 日志尾默认展开长度 | **40 行 / 4KiB** 截断 |

---

## 6. DOC-04 准入

### 6.1 影响矩阵行（STABILIZATION §3）

| 面 | 影响 | 档位 |
|----|------|------|
| 桌面壳 / 聊天呈现 | unified 确认卡、process/RunningCard | P0 |
| 项目侧栏 | Services 块；证据条 | P0 |
| WS / sidecar 事件 | 可选 `tool.progress` / `services.state` | P1 |
| evolve 工具语义 | 基本不改；prompt/catalog 一句对齐 | P1 |
| host / 计划门 / Progress Gate 硬逻辑 | **无**（只展示） | — |

### 6.2 回归 ID（预留）

| ID | 场景 |
|----|------|
| **IT-90** | 确认 y 后出现 RunningCard；tool.end 更新完成/失败（前端单测或组件测） |
| **IT-91** | `run_service` start 失败时聊天可见 logs_tail 或错误摘要 |
| **IT-92** | 侧栏 Services 列出登记服务；刷新后 alive 变化 |
| **IT-93** | 侧栏展示本回合证据条目（M1） |
| **S-90** | 手工：同意起后端 → 看见运行中 → 失败/成功可读；侧栏有服务行 |

---

## 7. 里程碑

| 里程碑 | 内容 | 状态 |
|--------|------|------|
| **doc** | 本文 + MAP/TASKS | **done（本文）** |
| **M0** | 聊天 RunningCard + 确认文案；侧栏 Services 刷新 | todo |
| **M1** | `tool.progress` / `services.state`；证据条；长驻日志默认展开策略打磨 | todo |
| **M2** | pet 壳对齐（可选）；日志「跟随」刷新 | defer |

---

## 8. 任务拆分（见 TASKS Phase 27）

T-2701 文档 · T-2702 D1～D3 确认 · T-2703 聊天 RunningCard · T-2704 确认文案 · T-2705 侧栏 Services · T-2706 事件/证据 M1 · T-2707 提示词对齐 · T-2708 IT/S 留痕

---

## 9. 修订记录

| 版本 | 日期 | 说明 |
|------|------|------|
| 0.1.0 | 2026-08-02 | 初稿：聊天+侧栏双面；M0/M1；DOC-04；默认 D1–D3 |
