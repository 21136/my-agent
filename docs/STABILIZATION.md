# 稳定化专项（STABILIZATION）

> 版本 **1.1.0** · 2026-07-18  
> **状态**：**done · 已解冻**（T-1890-10 用户签字；可恢复 feature Phase；新 Phase 须 DOC-04 / §9.3）  
> 关联：[BUGS.md](./BUGS.md) · [TASKS.md](./TASKS.md) §Phase 18 · [DESKTOP.md](./DESKTOP.md) · [RUNTIME.md](./RUNTIME.md) · [CONFIRM-PIPELINE.md](./CONFIRM-PIPELINE.md) · [TURN-CONTROL.md](./TURN-CONTROL.md) · [RUNTIME-GUARDS.md](./RUNTIME-GUARDS.md) · [CHECKER-SUBAGENT.md](./CHECKER-SUBAGENT.md) · [FILES-DROP.md](./FILES-DROP.md) · [HOST-SCOPE.md](./HOST-SCOPE.md) · [PROJECT-MODE.md](./PROJECT-MODE.md) · [DAILY-SHELL.md](./DAILY-SHELL.md) · [PET-SHELL.md](./PET-SHELL.md) · [stabilization-backlog.md](./stabilization-backlog.md) · [stabilization-log.md](./stabilization-log.md)

---

## 0. 为什么要开这个 Phase

2026-07-11～07-14 桌面联调累计 **BUG-001～019**；Phase 9～17 功能面快速扩张，但大量条目标 **done** 同时标注 **「待桌面手工验收」**——主路径未走通就进入下一 Phase，缺陷在接缝处爆发。

**结论**：继续叠功能会 **指数级增加接缝 bug**。Phase 18 目标不是新能力，而是 **把已有能力焊牢**，再恢复 feature 开发。

**v0.1 草案缺口**：只写了 grow+project 冷启动 12 条 smoke，未覆盖 daily/pet、活动路由、托管区、evolve/checker、CLI 对等、数据完整性、进程锁等。**v0.2 用「覆盖矩阵 + 分档」补齐功能表面。**

**v0.2 仍缺的维度（v0.3 补）**：功能之下的 **平台韧性面** —— LLM/网络异常、数据损坏静默降级、可观测性（sidecar 无日志落盘）、Windows 编码、资源增长、环境安装、测试隔离、secrets 泄漏回归。依据实地核查：`server.py` **没有**日志文件（崩溃后无证据）；`evolve_log.jsonl` **无轮转**；`session.py` 对坏 `messages.jsonl` 行 / 坏 `meta.json` **静默跳过或回退默认**（行为无测试）；PowerShell 终端已出现过 GBK 乱码输出。

---

## 1. 问题画像（五类根因）

| 类 | 典型现象 | 根因 | 预防手段 |
|----|----------|------|----------|
| **A · 接线** | ImportError、缺 import、导错模块（BUG-015/019） | 多入口（`main` / `server` / `project_api` / `host_scope_api`）无契约检查 | 模块契约表 + import 扫描 |
| **B · 异步接缝** | confirm 卡死、TURN_LOCK、状态谎报（BUG-002/006/008～013） | WS 读循环 × 工作线程 × 四壳前端状态机 | 集成测试 + 事件序断言 |
| **C · 生命周期** | sidecar 起不来、Electron/Vite、pet↔工作台断开（BUG-003/007/016） | 多窗三进程 + `interface_lock` | smoke 含冷启动/重连/真退出/忙时退出 |
| **D · 规则误伤** | guard 拦错路径、计划门提示吓人（BUG-018、计划门 UX） | 守卫与产品文案未对齐设计 | 用例表对齐 guards / project / checker |
| **E · 完成定义松** | 文档 done +「待手工验收」并存 | 无统一放行门槛 | 本文件 §9 + `stabilization-log.md` |

痛区不单是 project 壳，而是 **四壳 × 多会话线 × confirm × 路由 × host × evolve** 的叠加。

---

## 2. 目标与非目标

### 2.1 目标（Phase 18 完成时）

1. **覆盖矩阵**（§3）每一行有明确档位（P0/P1/P2/defer）与验收手段（smoke / IT / DOC）。
2. **P0 smoke**（§5.1）连续 3 次手工验收通过（不同日期或冷启动后）。
3. **P1 smoke**（§5.2）至少 1 次全 pass，或每条有 open BUG + 绕行说明。
4. **自动化**：§6 中 **Gate 集**（IT-G）一键绿；**扩展集**（IT-X）可 defer **≤ 3 条**，且须 **非 P0、非安全类**（与 §11 放行标准一致）。
5. **零 open P0**；P1 清零或显式绕行。
6. **「待手工验收」清零或转 open bug**（含 Phase 14/15/16/17 尾巴）。
7. **模块契约**（§7）+ CLI/桌面元命令 parity（§8）可查、可测。

### 2.2 非目标（本 Phase 不做）

- 新壳、新 guard、新 evolved、skill、checker M2+、pet M2（位置拖拽等）
- 大规模架构重构（合并 `main`/`server`）— 记 defer → Phase 19+
- 性能优化、纯 UI 美化（除非阻塞 smoke）
- 替用户做业务项目（斗地主等）
- **不要求一次焊完所有 P2**：P2 可带入放行后的维护 backlog

### 2.3 冻结规则（评审通过后生效）

| 冻结 | 例外 |
|------|------|
| 不新开 Phase 19+ 功能 task | 稳定化 task（T-18xx）、P0/P1 hotfix |
| 不扩 WS 协议字段 | 修 bug 必需的最小载荷修正 |
| 不加新 shell / 不启 pet M2 | govern 仅保证「不崩 + 可回 grow」 |
| 不新增 guard 类别 | hotfix 允许 **最小 guard 规则修正**（既有类别内） |
| 文档：以本文件为主；被 task 点名的同步 | `BUGS.md` / `stabilization-log.md` 随执行持续更新，不受此条限制 |

---

## 3. 覆盖矩阵（全表面 · 分档）

> **含义**：稳定化要「看见」的每一面。未列入矩阵的功能，默认 **不在放行范围内**，或标 defer。  
> **档位**：`P0` 阻塞放行 · `P1` 放行前尽量绿 · `P2` 可记 backlog · `defer` 明确不做。

### 3.0 壳合并后废止（DOC-05 · 2026-08-04）

> 前端已 **unified + pet 工作台**（[SHELL-CONSOLIDATION.md](./SHELL-CONSOLIDATION.md)）。下列矩阵行保留作 **Phase 18 历史验收记录**，**勿**再作为新 Phase 回归或技术债排期依据。现行验收见 [MAP.md](./MAP.md) §2.2 · [TASKS.md](./TASKS.md) DOC-05。

| 废止面 | 原 §3 引用 | 替代 |
|--------|------------|------|
| grow ↔ daily / project **DOM 切壳** | §3.1 S-10 · S-16 · S-13「daily 壳」 | `unified` perspective（default / project / night） |
| **`ui.route` / activity_router 切壳** | §3.2 IT-08 · S-45 | 已移除；主题仍可由 `infer_topic_scope` 追加 |
| **`govern` 壳** | §3.1 S-47 · T-904h | **cancelled** — 聊天 + `my-agent review` |
| **四壳 confirm 分壳** | §3.1 S-23「daily/pet confirm」 | unified + pet 共用 `chat-state` |
| **pet↔grow 串线（STD-001）** | BUG-020 | **fixed**；现行为 workbench ↔ pet + `shell_sessions` 标签 |

**仍有效（措辞需更新）**：project 生命周期 S-06～S-09（UI = unified `perspective=project`）· confirm/Stop S-04/S-05 · host/evolve/checker §3.4～3.5 · 数据韧性 §3.9。

### 3.1 壳与会话线

| 面 | 档 | 验收 | 说明 |
|----|----|------|------|
| grow 基础回合 / 新会话 / confirm / Stop | P0 | S-01～S-05 | 已有 |
| project 新建→计划→确认→切换 | P0 | S-06～S-09 | 已有 |
| project↔grow 壳切换 | P0 | S-10 | 已有 |
| grow↔daily 会话隔离 | P0 | S-16 | **v0.1 缺** |
| daily 基础回合 + confirm | P0 | S-13 | **v0.1 缺** |
| pet 发消息 + 工作台切换 | P0 | S-14 | BUG-016 前线 |
| 忙时关窗确认 + sidecar 清理 | P0 | S-17 | 仅 idle 退出不够 |
| 托盘真退出 | P0 | S-12 | 已有 |
| 跨项目切换确认卡 | P1 | S-20 | `project.switch.request` |
| plan.request 卡（非仅侧栏按钮） | P1 | S-19 | |
| project 一键验收 | P1 | S-21 | |
| draft 拒写码 / plan_dirty 再确认 | P1 | S-33 / S-34 | |
| 忙时禁止 project.switch | P1 | S-43 | |
| project 壳内 `新会话` | P1 | S-44 | |
| grow proposals accept/reject | P1 | S-15 | grow 核心价值 |
| 四壳 Stop | P1 | S-28 | S-05 仅 grow |
| daily/pet confirm | P1 | S-23 | |
| confirm 中 Stop | P1 | S-25 | |
| confirm 90s 超时 | P1 | S-26 | BUG-014 |
| 重连后状态一致 | P1 | S-22 | |
| Electron 闪退后 dev 存活 | P1 | S-18 | BUG-003 |
| pet→daily 映射说明 | P2 | DOC-01 | 架构缝 |
| govern 占位不崩 + 回 grow | P2 | S-47 | |
| pet M2（拖位/未读等） | defer | — | 维持 TASKS defer |

### 3.2 协议与路由

| 面 | 档 | 验收 | 说明 |
|----|----|------|------|
| `emit_session_state` / history+memory+banner | P0 | IT-06 / S-03 / S-09 | |
| import 模块契约 | P0 | IT-06 | BUG-019 类 |
| `ui.route` / activity_router | P1 | IT-08 / S-45 | T-906 大缝 |
| `session.refresh` 载荷完整 | P1 | IT-09 | |
| `command` ≡ `user.message` 元命令 | P1 | IT-11 | T-1808 |
| `project.open` vs `switch` | P1 | IT-12 | |
| `host_scope.*` WS | P1 | IT-13 / S-39 | Phase 10 全空 |
| 流式事件序 / error 后可输入 | P1 | IT-15 / IT-16 | BUG-004 类 |
| `session.list` / `open` | P2 | IT-10 | |
| `checker.verdict` / explore.progress | P1 | IT-14 | |

### 3.3 Confirm / Turn / Feedback

| 面 | 档 | 验收 |
|----|----|------|
| grow `write_text` confirm | P0 | S-04 / IT-03 |
| write_evolve 失败→二次 confirm | P1 | S-24 |
| host 写 confirm | P1 | S-27 |
| 跨会话 `read_file` confirm | P1 | IT-17 |
| stale 确认卡 | P1 | IT-03 |
| recall / 压缩 notice / memory 顶栏 | P1 | S-29～S-31 |
| `只聊`/`动手` | P2 | S-32 |

### 3.4 Guards / Evolve / Checker

| 面 | 档 | 验收 |
|----|----|------|
| 内联写入硬顶 + guard 日志不崩 | P1 | IT-21（复用 `test_runtime_guards.py`，挂 Gate runner） |
| scaffold 窄域拒调 / tool.toml 后 demo | P1 | IT-22 / IT-23（复用 `test_runtime_guards_m1.py`，挂 Gate runner） |
| grow 造工具最小路径 | P1 | S-35 |
| 自动/手动 checker + 完成声明门 | P1 | S-36～S-38 / IT-24～IT-25 |
| stall watchdog / Stop 杀 subprocess | P1 | IT-18 / IT-19 |
| explore 子代理 | P2 | IT-26 |
| proposal 落盘 | P1 | IT-27 / S-15 |

### 3.5 Host / 拖放 / 文件

| 面 | 档 | 验收 |
|----|----|------|
| project 拖放 | P1 | S-11 |
| 托管区向导 + 添加目录 | P1 | S-39 |
| host 只读 / denylist | P1 | S-40 / IT-28 |
| grow `_drops` / daily·pet 拖放 | P1 | S-41 / S-42 |
| file.unstage / 边界错误 / host 免复制 | P2 | IT-30～IT-32 |
| 跨 host root copy | P2 | IT-29 |

### 3.6 CLI 对等 / 进程 / 数据

| 面 | 档 | 验收 |
|----|----|------|
| CLI ↔ 桌面元命令 parity 表 | P0 | IT-38 / DOC-02 |
| `interface_lock` 冲突提示 | P1 | S-46 / IT-39 |
| 孤儿 `tool_calls` repair | P1 | IT-42 |
| history 过滤规则 | P1 | IT-43 |
| host_scope 落盘往返 | P1 | IT-44 |
| CLI confirm 不空转 / exit 归档 | P2 | IT-40 / IT-41 |
| 用户扩展主题 | P2 | IT-49 |
| evolve_log / bootstrap 事件序 | P1 | IT-45 / IT-47 |
| 流式错误体 / mode-budget segment | P2 | IT-46 / IT-48 |

### 3.7 工程治理（流程面 · 与代码同等重要）

| 面 | 档 | 交付 |
|----|----|------|
| 「done」定义：代码 + 自动化 + smoke 记录 | P0 | DOC-03 · 改 `TASKS` 约定 |
| 新 Phase 准入：须附回归 smoke id | P0 | DOC-04 |
| Bug 进门：必填复现 + 根因类 A～E | P0 | `BUGS.md` 模板强化 |
| 改代码后强制冷启动提醒 | P0 | 已有；smoke 强制执行 |
| 已知「implemented · 待验收」清册 | P0 | §10 开放项 |
| 文档/代码漂移审计（协议表 vs `server.py`） | P1 | T-1813 |

### 3.8 LLM / 网络层（v0.3 新增）

| 面 | 档 | 验收 | 说明 |
|----|----|------|------|
| API key 失效 / 配额 / 5xx → 用户可读错误 | P0 | S-48 | BUG-004 类；错误必须到 UI，不假死 |
| LLM 超时 → `finish_reason=timeout` 全链路 | P1 | IT-51（复用 T-1519 测试） | 已实现，纳入 Gate |
| 断网发消息 | P1 | S-49 | 合意报错 + 可重试；不卡「思考中」 |
| flash/pro 模型切换（主题绑定） | P2 | IT-52 | `resolve_session_model` |
| 上下文超限 → digest 压缩后回合可续 | P1 | IT-53 / S-30 | 压缩正确性，不丢关键锚定块 |
| `web_search` / `fetch_url` 失败降级 | P2 | IT-54 | 上游挂了不炸回合 |

### 3.9 数据韧性 / 损坏恢复（v0.3 新增）

| 面 | 档 | 验收 | 说明 |
|----|----|------|------|
| 坏 `messages.jsonl` 行 → 跳过且**告知** | P1 | IT-55 | **已实现**（T-1823-02）：`corruption_notices` + `turn.notice`；方案见 §3.9.1 |
| 坏 `meta.json` → 回退默认且**告知** | P1 | IT-55 | **已实现**（T-1823-04）：结构失败 → `corruption_notices`；方案见 §3.9.2 |
| 坏 `data/state.json` → 不崩、可重建 | P1 | IT-56 | **已实现**（T-1823-05）：降级 `{}` + `paths.corruption_notices` / `turn.notice` |
| 半写入（崩溃时 save 中断） | P2 | IT-57 | 评估原子写（tmp+rename）；至少文档化风险 |
| meta/state schema 变更兼容旧会话 | P1 | DOC-05 | **已落地** [RUNTIME.md](./RUNTIME.md) §2.4（T-1823-06） |
| `data/` 不在 git：误删无恢复 | P2 | DOC-06 | **已落地** §3.9.4（T-1806-doc-06）；备份建议，非自动实现 |

#### 3.9.1 坏 `messages.jsonl` 行 · 用户可见 notice（T-1823-01 已决）

> 全文与实现清单见 [`stabilization-log.md`](./stabilization-log.md) · T-1823-01。此处为契约摘要，供 T-1823-02 实现对照。

| 项 | 已决 |
|----|------|
| **降级策略** | 保持：非法 JSON / 非 object 行 **跳过**，不 crash、不改写磁盘（跳过本身不 rewrite jsonl） |
| **单元表面** | `Session.corruption_notices: list[str]`（非空 = 本会话加载时有跳过）；IT-55 已绿（T-1823-02） |
| **用户表面** | WS `{ type: "turn.notice", level: "warn", text }`；桌面已有 notice 块，**不需**新协议 type |
| **发射时机** | 仅在 **真加载** 路径：WS connect / `shell.switch` / `session.open` / project `session_replaced`；在 `emit_session_state`（含 `session.history`）**之后**再 emit（history 会 `blocks = …` 替换聊天区） |
| **不发射** | 仅 `session.refresh`（不重读 jsonl）不单独依赖；无跳过时不发；**不用** `error`（过重且 reset busy） |
| **文案** | 含跳过行数；可选行号；**不落盘** `messages.jsonl`（对齐 TURN-FEEDBACK A4） |
| **CLI** | 加载后打印一行 muted（parity） |
| **明确不做（本项）** | 新 `session.corruption` type；扩展 `session.banner` 字段；自动备份/修复坏行 |

#### 3.9.2 坏 `meta.json` · 用户可见 notice（T-1823-03 已决）

> 全文见 [`stabilization-log.md`](./stabilization-log.md) · T-1823-03。实现对照 T-1823-04。

| 项 | 已决 |
|----|------|
| **降级策略** | 保持：不可读 JSON / 非 object /（`Session.load` 时）缺失文件 → **默认 `SessionMeta`**，不 crash；**加载本身不改写**坏 `meta.json` |
| **何谓「坏」** | 结构失败：`JSONDecodeError` / `OSError` / 根值非 object；以及 load 时 **文件缺失**。合法 JSON object 但字段缺省/类型不对 → **非**本项（归 DOC-05 / `from_dict` 默认） |
| **单元表面** | **同** `Session.corruption_notices`（可与 jsonl notice **并存**多条） |
| **用户表面** | **同** §3.9.1：`turn.notice` + `level: "warn"`；发射点已由 T-1823-02 接好，T-1823-04 **只需**在 load 填 notices |
| **文案重点** | 明确 **主题 / 壳 / 项目绑定可能已丢失**，提示核对顶栏 |
| **磁盘副作用** | 下次 `save()` / `_write_meta` 会用内存默认覆盖坏文件（既有行为）；本 Phase **不做**自动备份 |
| **明确不做** | 新协议 type；尝试从坏文件「部分抢救」字段；改 `session.banner` 语义 |

#### 3.9.3 坏 `state.json` · 用户可见 notice（T-1823-05）

| 项 | 已决 / 实现 |
|----|-------------|
| **降级** | 不可读 / 非 object → `{}`；**缺文件**为常态，不 notice |
| **单元表面** | `AgentPaths.corruption_notices`（IT-56；与 Session 列表分工：全局索引 vs 会话文件） |
| **用户表面** | `corruption_notice_events` 合并 paths notices → 同 `turn.notice` warn |
| **读路径** | 统一 `paths.read_agent_state_payload`（shell / project / last_conversation_id） |

#### 3.9.4 DOC-06 · `data/` 备份建议（**已定 · T-1806-doc-06**）

> **一句话**：会话、壳映射、托管区配置在 **`data/`**，默认 **不进 Git**（见根目录 `.gitignore`）。`git clone` / `pull` **不会**带回你的聊天与本机状态；误删或换盘无 Git 可回滚。

| 路径 | 是否 git | 丢了会怎样 | 备份优先级 |
|------|----------|------------|------------|
| `data/sessions/<id>/` | **否** | 聊天历史、meta 没了 | **高** |
| `data/state.json` | **否** | 最近会话、`shell_sessions`（grow/daily）、项目索引丢失 | **高** |
| `data/host_scope.json` | **否** | 托管目录列表要重配 | **高** |
| `data/evolve_log.jsonl` | **否** | 审计/引用轨迹断档（evolve 本体仍在 Git） | 中 |
| `data/logs/` | **否** | 仅取证损失 | 低 |
| `data/conversations/` | **否**（可选录制） | `exit --record` 摘要/全文没了 | 按需 |
| `workspace/` | **否** | 项目产物丢失 | **高**（若在做项目） |
| `evolve/` · `docs/` · `agent-core/` | **是** | 用 `git` 恢复 | — |

**建议做法**（本 Phase **不**做自动备份工具）：

1. **定期**：把整个 `data/`（及需要的 `workspace/`）打成 zip / 复制到另一盘或网盘；命名带日期，如 `my-agent-data-20260718.zip`。
2. **换机前**：先备份再 `git clone`；新机装依赖 + API key 后，把备份的 `data/`（可选 `workspace/`）拷回同相对路径。
3. **勿**为「进 Git」强行 `git add data/sessions` — 可能含隐私；需要可追溯摘要时用 CLI `exit --record`（见 [PROJECT.md](./PROJECT.md) §6.4）。
4. **与 evolve 分工**：工具/提示词真源仍是 **Git 上的 `evolve/`**；`data/` 只保本地运行态。回滚工具用 `git`，恢复聊天用本备份。

交叉引用：[PROJECT.md](./PROJECT.md) §6.1 · 根 `.gitignore` · 资源清理见 **§3.10.1 DOC-07**。

### 3.10 可观测性 / 资源（v0.3 新增）

| 面 | 档 | 验收 | 说明 |
|----|----|------|------|
| sidecar 日志落盘（崩溃后可取证） | **P0** | T-1805-01～07 / IT-58 | 现状：仅 stdout，托盘启动崩溃即无证据 |
| 未捕获异常 → 日志 + WS error 双写 | P1 | IT-58 | 对齐 `_run_line` 的 except 路径 |
| `evolve_log.jsonl` 无轮转 | P2 | DOC-07 / IT-59 | **策略已文档化** §3.10.1（轮转实现 defer） |
| `data/sessions/` 堆积（含测试残留） | P2 | DOC-07 | **策略已文档化** §3.10.1（T-1806-doc-07） |
| `data/repl_sessions/*.pkl` 残留 | P2 | DOC-07 | **策略已文档化** §3.10.1 |
| secrets 不进日志（回归） | P1 | IT-60（复用 `sanitize_log_value` 测试） | 已有实现，纳入 Gate 防回归 |

#### 3.10.1 DOC-07 · 资源增长与清理（**已定 · T-1806-doc-07**）

> **一句话**：本 Phase **不做**自动清盘/轮转 `evolve_log`；运营者按表手动删安全残留，**勿**误删仍在用的真实会话（先对照 `data/state.json` · `shell_sessions` / `project_sessions`）。  
> 备份优先于清理：见 §3.9.4 DOC-06。

| 路径 | 增长原因 | 自动策略（现状） | 手动清理（安全做法） |
|------|----------|------------------|----------------------|
| `data/evolve_log.jsonl` | 每次 tool / 治理 / checker 等追加一行；**无轮转** | **无**（IT-59 轮转 defer） | 体积过大时：先**复制**为 `evolve_log-YYYYMMDD.jsonl.bak`，再截断为空文件或只留近 N 天（按行 `ts` 过滤）；**勿**在 sidecar/CLI 运行中直接删半截文件 |
| `data/sessions/<id>/` | 每开新会话一目录；测试曾写大量 `_*` | Gate 高危已隔离（T-1824-05）；**历史 `_*` 不自动清** | **可删**：确认不在 `state.json` 映射中的 `_*` / `_demo*` / `_parity*` / `_guard*` 等测试目录。**勿删**：`shell_sessions` / `project_sessions` / `last_conversation_id` 指向的 id |
| `data/repl_sessions/*.pkl` | 旧 REPL / bootstrap / debug 产物 | **无**自动删 | 可整目录删 `bootstrap.pkl` · `debug.pkl` · `chk.pkl` · `run1.pkl` · `t2.pkl` 等；不影响桌面 WS 会话线 |
| `data/logs/sidecar-*.log` | 日日志 | **已轮转**：单文件 ≥10MB → `.1`…`.5`（T-1805-05 · [DESKTOP.md](./DESKTOP.md) §4.4.3） | 可删过期日文件；保留近几日便于取证 |
| `data/sessions/<id>/tool_outputs/` | 超长 tool 结果落盘 | 单次上限见 TOOLS | 可删**旧会话**下该目录；当前会话删了仅影响回顾附件 |
| `workspace/_drops/` · `_incoming/` | 拖放暂存 | 无自动 GC | 确认已处理后可删对应会话子目录 |

**硬规则**：

1. **清盘 ≠ 本任务默认动作**：稳定化验收留下的 `data/sessions/_*` 可择机清，但**非**放行阻塞；清前先备份（DOC-06）。
2. **真实会话**：grow / daily / project 映射 id **禁止**批量删；不确定就只删明确 `_` 前缀测试残留。
3. **evolve_log**：截断前备份；治理 `failure_streak` / 审计依赖近期行，截断过狠会丢 streak 上下文（可接受则记下日期）。
4. **本 Phase 不做**：定时 GC 进程、`evolve_log` 自动轮转实现、一键「清理测试会话」UI。

交叉引用：§3.9.4 DOC-06 · [DESKTOP.md](./DESKTOP.md) §4.4.3 · T-1824-04/05 隔离 · [`data/_t1824-04-audit.md`](../data/_t1824-04-audit.md)（污染源清单，历史参考）。

### 3.11 环境 / 平台（v0.3 新增）

| 面 | 档 | 验收 | 说明 |
|----|----|------|------|
| Windows 控制台编码（GBK 乱码） | P1 | S-50 / DOC-08 | 终端已实际出现乱码；CLI 输出与日志统一 UTF-8 策略 |
| 中文/空格路径的项目 id、拖放文件名 | P1 | IT-61 | `normalize_project_id` 已限 ASCII；拖放中文文件名未测 |
| 新机器 bootstrap（fresh clone → 可跑） | P1 | S-51 / DOC-09 | **已落地** [README](../README.md) DOC-09 · §3.11.1（T-1806-doc-09） |
| `start-desktop.bat` 双击两次 / 双实例 | P1 | S-52 | 端口 8765 冲突 + `interface_lock` 行为 |
| Python 版本下限（3.12+）不符时的报错 | P2 | DOC-09 | **已文档化**：先查 `python --version`；bat 无 Python 时提示安装 3.12+ |
| 测试隔离：单测不污染真实 `data/` | P1 | IT-62 | 现有 demo 测试直接建删真实目录；Gate 前审计 |

#### 3.11.1 DOC-09 · Bootstrap 清单（**已定 · T-1806-doc-09**）

> **真源步骤表**：[README.md](../README.md)「DOC-09 · Fresh bootstrap」。此处为矩阵摘要与交叉引用。

| 项 | 约定 |
|----|------|
| **路径** | clone → 确认 Python **3.12+** → `pip install -r requirements.txt` →（桌面）`start-desktop.bat` 自动 npm → 见 `{"ready": true}` |
| **依赖真源** | 根 `requirements.txt`：`httpx` + `websockets`（S-51 曾缺 httpx，已补） |
| **CLI 编码** | 必须 `start.bat`（DOC-08）；裸跑易乱码 |
| **data** | 新 clone **无**会话；换机拷备份见 §3.9.4 |
| **验收烟** | S-51 / T-1821-17：临时树 pip+npm → sidecar ready + WS `session.banner` |
| **非目标** | 本 Phase **不做**安装向导 GUI；不做自动检测并升级 Python |

---

## 4. 分期交付

```text
M0 文档定稿（T-1801～T-1803, T-1813）
  → 覆盖矩阵评审 · 契约 · done 定义 · 协议漂移审计清单

M1 P0 焊死（T-1804～T-1806, T-1810, T-1814～T-1816）
  → Gate 测试绿 · P0 smoke 3 次 · CLI parity · daily/pet/路由最小集

M2 P1 清缝（T-1807～T-1809, T-1811, T-1817～T-1820）
  → host/evolve/checker/guards 纳入 runner · 开放项闭环

M3 放行（T-1812）
  → P0 全绿 · P1 无裸奔 · 解除冻结
```

---

## 5. Smoke 清单

环境：Windows · `start-desktop.bat` · **完全退出后冷启动**。结果记入 [stabilization-log.md](./stabilization-log.md)。

### 5.1 P0（阻塞放行 · 须连续 3 次全 pass）

| ID | 路径 | 步骤摘要 | 期望 |
|----|------|----------|------|
| S-01 | sidecar 启动 | 启动桌面 | 无 ImportError；WS 就绪 |
| S-02 | grow 基础回合 | grow 发「1+1」 | `turn.end` ok；可继续输入 |
| S-03 | 新会话 | 发 `新会话` | 秒回就绪（BUG-007） |
| S-04 | confirm | grow `write_text` → 同意 | 60s 内 confirm.done + tool.end + turn.end |
| S-05 | Stop | 长回合点 Stop | 3s 内 turn.end cancelled |
| S-06 | project 新建 | `新会话` → `项目 新建 smoke-demo` | 三件套 + 计划待确认 |
| S-07 | project 出计划 | 「填 PROJECT.md 和 TASKS.md」 | 可写三件套；无假「整轮已拦截」 |
| S-08 | project 确认 | 侧栏「确认开工」 | confirmed；顶栏 n/m |
| S-09 | project 切换 | 第二项目侧栏切换 | 无 ImportError；history 替换（BUG-019） |
| S-10 | 壳切换 | project → grow → project | 独立会话 |
| S-12 | 真退出 | 托盘退出 | python sidecar 结束 |
| S-13 | daily 基础 | 切 daily · 短问答 · 一次小写确认 | turn.end；confirm 不卡 |
| S-14 | pet | 伴侶发消息 → 开工作台再回 | 无 connection handler 刷屏崩态；可续聊 |
| S-16 | grow↔daily | 各聊一句再互切 | 聊天区不串线 |
| S-17 | 忙时退出 | 回合中点退出 | 确认框；结束后无残留 python |
| S-48 | LLM 异常 | 临时改坏 API key 发消息 | 用户可读错误；不假死；恢复 key 后可续 |

**P0 通过**：上表全 `pass`（S-11 拖放默认 P1；若你以拖放为主路径可升为 P0）。

### 5.2 P1（放行前至少 1 次全过，或每条 BUG+绕行）

| ID | 路径 | 期望要点 |
|----|------|----------|
| S-11 | project 拖放小文件 | staged / 合意错误；不崩 |
| S-15 | grow proposals 接受/拒绝 | 队列更新；不卡死 |
| S-18 | Electron 闪退后 Vite 仍在 | 可再起壳 |
| S-19 | plan.request 卡确认 | 与侧栏确认等价 |
| S-20 | 跨项目切换确认卡 | 取消不换；确认才换 |
| S-21 | project.verify | confirmed 后跑验收 |
| S-22 | 断线重连 | banner/history 一致 |
| S-23 | daily/pet confirm | 同 S-04 语义 |
| S-24 | write_evolve 二次 confirm | 不空转（BUG-008） |
| S-25 | confirm 中 Stop | choice=cancelled |
| S-26 | confirm 90s 超时 | confirm.done；可再聊 |
| S-27 | host 写 confirm | 绝对路径标签正确 |
| S-28 | project/daily/pet Stop | 同 S-05 |
| S-29 | recall | 「刚刚说了什么」无乱调工具 |
| S-30 | 压缩 | notice 可见；非假死 |
| S-31 | memory 顶栏续接 | 条数合理 |
| S-33 | draft 拒写码 | run_python / 源码写被拒 |
| S-34 | plan_dirty 再确认 | 变更后须再确认 |
| S-35 | grow 造工具最小路径 | write_evolve→可加载 |
| S-36～S-38 | checker 自动/手动/声明门 | notice + 非 PASS 不得「已验收」 |
| S-39～S-40 | 托管区向导 + host 只读 | 设置可加目录；denylist 生效感 |
| S-41～S-42 | grow/daily/pet 拖放 | 不崩 |
| S-43 | 忙时 project.switch | 被拒或排队说明 |
| S-44 | project 壳新会话 | 绑定不串旧项目聊天 |
| S-45 | 壳锁定 | 忽略 auto `ui.route` |
| S-46 | CLI+桌面双开 | lock 提示清晰 |
| S-49 | 断网发消息 | 合意报错；可重试；不卡「思考中」 |
| S-50 | CLI 中文输出 | PowerShell 无乱码（编码策略 DOC-08） |
| S-51 | fresh bootstrap | 新目录 clone → 依赖安装 → 首启可用 |
| S-52 | 双实例 | 二次启动有清晰提示，不静默抢端口 |

### 5.3 P2（可 backlog）

| ID | 路径 |
|----|------|
| S-32 | 只聊/动手 |
| S-47 | govern 占位 + 回 grow |

---

## 6. 自动化集成测试

### 6.1 Gate 集（IT-G · `run_stabilization.py` 必绿）

> **Gate 成员资格与 §3 档位独立**：Gate 全绿是放行硬条件，不随所覆盖面的 P1/P2 档位降级（自动化成本低，故可比手工 smoke 更严）。
>
> **实现**：`agent-core/tests/run_stabilization.py` · `GATE_MODULES` + `GATE_CHECKER_TARGETS`（T-1807-01～03）。
> **分项摘要**：跑完后打印 `Gate summary (IT-G):` 表（T-1807-02）。
> **exit 0**：全部模块 PASS（含 `expectedFailure` / xfail）；任一 `fail`/`err`/`uxpass` → exit 1。

#### 6.1.1 IT 覆盖（与 runner 对齐 · 2026-07-17）

| ID | 范围 | 覆盖 | Gate 状态 |
|----|------|------|-----------|
| IT-01 | project 生命周期 | S-06～S-08 | `test_project_lifecycle` |
| IT-02 | project.switch 事件 | S-09 | `test_project_switch` |
| IT-03 | confirm 错 ID / 超时 / stale | S-04 / S-26 | `test_confirm_pipeline` |
| IT-04 | shell_switch 隔离 | S-10 / S-16 | `test_cross_session_read` |
| IT-05 | turn.cancel | S-05 | `test_turn_cancel` |
| IT-06 | import 契约 + emit_session_state（history+memory+banner，T-1803-05/06） | A 类 | `test_module_contracts` · `test_project_switch` |
| IT-08 | activity_router / ui.route | S-45 | `test_activity_router` |
| IT-17 | 跨会话 read confirm | T-1117 | `test_cross_session_read` |
| IT-21～IT-23 | guards M0/M1 | D 类 | `test_runtime_guards` · `test_runtime_guards_m1` |
| IT-24～IT-25 | checker 核心子集 | S-36～S-38 | `test_checker_subagent` **subset**（18 cases，见 §6.1.3） |
| IT-42 | repair_orphaned_tool_calls | BUG-005 | `test_orphaned_tool_calls` |
| IT-51 | LLM timeout → `finish_reason=timeout` | §3.8 / S-48 | `test_runtime_guards`（`LlmTimeoutChainTests`） |
| IT-55 | 坏 messages.jsonl 降级 | §3.9 | `test_session_corruption` · 跳过 + `corruption_notices` / `turn.notice`（T-1823-02） |
| IT-56 | 坏 `state.json` 不崩 | §3.9 | `test_session_corruption` · 降级 `{}` + `paths.corruption_notices`（T-1823-05） |
| IT-58 | sidecar 日志落盘 | T-1805 / §3.10 | `test_sidecar_logging` |
| IT-60 | secrets 不进日志 | §3.10 | `test_sanitize_log_value` |
| IT-38 | 元命令 parity 表断言 | §8 | `tests.test_cli_desktop_parity`（15 cases）· **未入 Gate** |
| IT-11 | `command` ≡ `user.message` 元命令 | §8 · IT-11 | `CommandUserMessageEquivalenceTests` in `test_cli_desktop_parity` |
| IT-62 | 测试隔离审计（不污染真实 `data/`） | §3.11 | **defer** → T-1824（未入 runner） |

#### 6.1.2 Gate 模块清单（`GATE_MODULES`）

与 `run_stabilization.py` 中 tuple **逐字一致**；摘要表每模块一行。

| 模块 | 批次 | IT |
|------|------|-----|
| `tests.test_sidecar_logging` | M1-C | IT-58 |
| `tests.test_project_lifecycle` | M1-D | IT-01 |
| `tests.test_project_switch` | M1-D | IT-02 · IT-06 |
| `tests.test_module_contracts` | M1-D | IT-06 |
| `tests.test_confirm_pipeline` | M1-E | IT-03 |
| `tests.test_cross_session_read` | M1-E | IT-04 · IT-17 |
| `tests.test_turn_cancel` | M1-E | IT-05 |
| `tests.test_activity_router` | M1-E | IT-08 |
| `tests.test_runtime_guards` | M1-F | IT-21 · IT-51 |
| `tests.test_runtime_guards_m1` | M1-F | IT-22～IT-23 |
| `tests.test_orphaned_tool_calls` | M1-F | IT-42 |
| `tests.test_sanitize_log_value` | M1-F | IT-60 |
| `tests.test_session_corruption` | M1-F | IT-55 · IT-56 |

#### 6.1.3 Checker 子集（`GATE_CHECKER_TARGETS`）

全文件 19 cases；Gate 跑 **18** — 省略 `HardChecklistTests.test_broken_manifest_fails`（TOML 解析边界）。

| 目标 | 类 / 方法 |
|------|-----------|
| `ParseCheckerCommandTests` | 全类 |
| `VerdictMergeTests` | 全类 |
| `HardChecklistTests` | `test_missing_tool_fails` · `test_write_text_passes_with_demo` |
| `CheckerTaskFromRecordTests` | 全类 |
| `CompletionGateTests` | 全类 |
| `CheckerRunnerTests` | 全类 |
| `AutoCheckerSpawnTests` | 全类 |

摘要标签：`tests.test_checker_subagent (subset)`。

#### 6.1.4 本地一键

```powershell
Set-Location agent-core
python tests/run_stabilization.py          # Gate 集 · exit 0 = 全绿
python -m unittest discover -s tests -p "test_*.py" -v   # 全量（可选）
```

**期望输出（2026-07-18）**：**117** tests · **0** `expected failure` · 末尾 `TOTAL OK 117 run`。

### 6.2 扩展集（IT-X · 尽量绿；≤3 条可 defer 且非安全项）

| ID | 范围 |
|----|------|
| IT-07 | govern shell.switch 不毁会话 |
| IT-09～IT-16 | refresh / session.list / command / project.open / host WS / streaming / error / checker notice |
| IT-18～IT-20 | stall / Stop+subprocess / turn.start intent |
| IT-26～IT-29 | explore / proposal / host denylist·copy（IT-24～25 已在 Gate 子集） |
| IT-30～IT-37 | file_stage 边界 / host 免复制 / project.list / after_turn hooks / 冷启动 shell_sessions |
| IT-39～IT-41 | interface_lock / CLI confirm / exit 归档 |
| IT-43～IT-50 | history 过滤 / host 落盘 / evolve_log / bootstrap / 流式错误 / segment / 扩展主题 / workflow host |
| IT-52～IT-54 | 模型切换 / digest 压缩续聊 / web_search·fetch_url 降级 |
| IT-57～IT-59 | 半写入 / 未捕获异常双写 / 日志轮转（IT-56 基线已在 Gate；IT-58 已在 Gate） |
| IT-61 | 中文文件名拖放 / 路径边界 |

---

## 7. 模块契约（防 A 类）

| 符号 | 定义模块 | 用途 |
|------|----------|------|
| `session_history_event` | `session.py` | 桌面灌聊天历史 |
| `session_memory_event` | `context.py` | 顶栏 memory |
| `session_banner_event` | `session.py` | 会话横幅 |
| `emit_session_state` | `server.py` | 连接/刷新推送 |
| `after_turn_project_hooks` | `project_api.py` | 回合后 project 状态 |
| `dispatch_project_message` | `project_api.py` | project.* / plan.* |
| host scope handlers | `host_scope_api.py` | host_scope.* |

**规则**：

1. 上表禁止抄错 import；新增事件先查表（`RUNTIME.md` §7.2）。
2. 用户可见元命令须走同一 `ConversationRepl.handle_line`（桌面 `user.message` / `command` 皆然）。
3. 切换项目只走 `project_switch`；列表/状态走 `project_api`；REPL 走 `project_cli`。
4. pet 前端壳 ≠ backend shell：pet 映射 **daily** 会话线（DOC-01）。

---

## 8. CLI ↔ 桌面 parity（DOC-02 / IT-38）

> **审计表（DOC-02 定稿）**：[CLI-DESKTOP-PARITY.md](./CLI-DESKTOP-PARITY.md) **v0.3.2**（T-1808-01～05 · IT-38 + IT-11）

| 能力 | CLI | 桌面 | 稳定化要求 |
|------|-----|------|------------|
| 新会话 / 换主题 / 压缩 | `handle_line` | `user.message` | 同路径 + emit_session_state |
| 项目 新建/切换/确认/验收/状态 | `项目 …` | 聊天命令 + 侧栏/WS | 行为一致 |
| 只聊 / 动手 | 元命令 | 同左 | 预算/工具可见性一致 |
| 托管目录 | `托管目录 …` | 设置/host_scope.* | 列表一致 |
| 验收 / check | CLI | notice / 无对等按钮时须文档说明 | 不静默失败 |
| confirm | stdin | 确认卡 | 皆不空转 |
| Stop / cancel | Ctrl 类（若有） | Stop 按钮 | 桌面为真源 |

审计输出：parity 表（缺项 = BUG 或 DOC 绕行）。详细族表 / 绕行文案 / 自动化见上链文档 §1 · §6 · `tests.test_cli_desktop_parity`。

---

## 9. Bug 与「done」原则

### 9.1 严重度

1. **P0**（崩、卡死、数据丢、锁死输入）：优先于一切 task。  
2. **P1**（主路径不可用但有绕行）：M2 必须修或 open+绕行。  
3. **P2**（体验/文案）：可 defer，**禁止误导**（假拦截、假完成）。

### 9.2 完成定义（DOC-03）

一项标 **done** 须同时：

- [ ] 代码合入  
- [ ] 相关 IT 绿或声明「仅手工」并挂 smoke ID  
- [ ] `stabilization-log` 或等价记录至少 1 次相关路径 pass  
- [ ] `BUGS`/`CHANGELOG` 已更新（若修缺陷）

禁止：仅「实现了」+「待桌面验收」长期挂账。

### 9.3 新 Phase 准入（DOC-04 · 解冻后）

开新功能 Phase 前须写明：影响哪些 §3 矩阵行、回归哪些 S-/IT- ID。缺省 = 评审驳回。

---

## 10. 开放项清点（进 M1/M2）

> **T-1808-bug-06（2026-07-18）**：下表逐条结论；**M2-H 完结**。仍 open 的产品债见 [stabilization-backlog.md](./stabilization-backlog.md)（**STD-001** · P1，非本表）。

| | 来源 | 项 | 动作 / 验收 | 结论 |
|---|------|-----|-------------|------|
| [x] | Phase 15 / BUG-014 | Stop · confirm 90s | S-05 / S-25 / S-26 / S-28 | **closed** · BUG-014 **fixed**（T-1808-bug-01） |
| [x] | Phase 16 | T-1517 cancel 45s | 并入 S-05 / S-28 | **closed**（T-1808-bug-02） |
| [x] | Phase 17 | 桌面 checker notice | S-36～S-38 | **closed**（T-1808-bug-03） |
| [x] | 计划门 UX | draft 填三件套 | S-07 / S-33 | **closed** · S-07 P0 三轮 pass · S-33 T-1821-01 pass |
| [x] | DESKTOP §5.3 旧清单 | turn.cancel 勾选过时 | 对照代码更新勾选 | **closed** · T-1813-04 / D-08 → §5.3 `[x]` |
| [x] | pet M2 | i4b～i7 | 维持 defer | **defer**（[PET-SHELL.md](./PET-SHELL.md)；非放行阻塞） |

---

## 11. 放行标准（T-1812）

同时满足：

- [x] §5.1 P0 smoke **连续 3 次**全 pass（`stabilization-log.md`）— **T-1890-01**
- [x] §5.2 P1：全 pass **或** 每条失败有 open BUG + 用户可见绕行 — **T-1890-03**（34/34 pass）
- [x] §6.1 Gate 集全绿 — **T-1890-02**（117 run · 0 fail）
- [x] §6.2 扩展集：失败 ≤3 且无 host denylist / confirm 空转 / 数据损坏类 — **无记录 fail**；显式 defer ≤3 且非安全：IT-38（未入 Gate）· IT-62（T-1824 已审计/隔离）· IT-59（DOC-07 轮转 defer）
- [x] `BUGS.md` 无裸奔 P0；P1 均有结论 — **T-1890-06**（STD-001 在 backlog，有绕行+拟修）
- [x] **sidecar 日志落盘（T-1805-01～07）已实现**——之后的崩溃才有取证能力 — **T-1890-04**
- [x] DOC-01～09 落地（05～09 为 v0.3 新增：schema 兼容、备份建议、资源增长、编码策略、bootstrap）— **T-1890-05**
- [x] `TASKS.md` Phase 18 任务 **done** — **T-1890-08**（Epic T-1801～1825 必做面全 done；T-1812 待 09～10；M2-I 整批 defer）
- [x] 用户确认可恢复 feature 开发 — **T-1890-10**（2026-07-18 · 用户签字：「同意解冻：可恢复 feature Phase」）

> **T-1890-07～10**：放行标准全齐；**feature 冻结已解除**。新 Phase 须遵守 §9.3 / DOC-04。

---

## 12. 与 TASKS 映射

- **粗粒度 Epic**：见 [TASKS.md](./TASKS.md) §Phase 18 索引表（T-1801～T-1825）。
- **细粒度执行清单（~146 项）**：见 **[STABILIZATION-TASKS.md](./STABILIZATION-TASKS.md)** — 按 `T-18MCC-NN` 逐条勾选验收。

手工记录：[stabilization-log.md](./stabilization-log.md)。

---

## 13. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0-draft | 2026-07-14 | 初稿：窄 grow+project smoke |
| 0.2.0-draft | 2026-07-14 | **全表面覆盖矩阵**；五类根因；P0/P1/P2 smoke；Gate/扩展 IT；CLI parity；done/准入治理；扩 task 至 T-1820 |
| 0.3.0-draft | 2026-07-15 | **平台韧性面** §3.8～3.11：LLM/网络异常、数据损坏降级告知、sidecar 日志落盘（新 P0）、资源增长、Windows 编码、bootstrap、双实例、测试隔离；S-48～S-52、IT-51～IT-62、DOC-05～09、T-1821 |
| **1.0.0** | 2026-07-15 | **定稿**（T-1800-01～06 评审通过）：§2.1 目标 4 明确 defer ≤3 且非 P0/非安全类；§2.3 补 guard 修正例外与 BUGS/log 文档例外；§3.5 S-11 P0→P1（与 §5.2 统一）；§3.4 IT-21～23 指明复用 `test_runtime_guards*` 挂 Gate runner；§3.10 P0 行验收改引 T-1805-01～07 / IT-58；§6.1 加「Gate 与档位独立」说明并点名 IT-06 载荷验收；§11 T-1821 改引 T-1805 |
| 1.0.1 | 2026-07-18 | §3.9.1：坏 jsonl 行 notice 已决（T-1823-01）；跳过保留 + `Session.corruption_notices` + `turn.notice` warn（history 之后） |
| 1.0.2 | 2026-07-18 | §3.9.2：坏 meta.json notice 已决（T-1823-03）；复用 corruption_notices 通道 |
| 1.0.3 | 2026-07-18 | §3.9.3：坏 state.json notice（T-1823-05）；`AgentPaths.corruption_notices`；Gate 0 xfail |
| **1.0.4** | 2026-07-18 | **DOC-06 / T-1806-doc-06**：§3.9.4 `data/` 不在 git + 备份建议 |
| **1.0.5** | 2026-07-18 | **DOC-07 / T-1806-doc-07**：§3.10.1 资源增长与清理（evolve_log / sessions / pkl） |
| **1.0.6** | 2026-07-18 | **DOC-09 / T-1806-doc-09**：§3.11.1 bootstrap；README 完整清单；**M2-G 完结** |
| **1.0.7** | 2026-07-18 | **T-1808-bug-06**：§10 开放项逐条勾选；**M2-H 完结** |
| **1.0.8** | 2026-07-18 | **T-1890-07**：文首状态 **done**；§11 勾选 T-1890-01～06 已满足项；末两行（TASKS 全表 · 用户签字）留给 T-1890-08～10 |
| **1.0.9** | 2026-07-18 | **T-1890-08**：§11 TASKS 行勾选；`TASKS.md` Epic 对齐；M2-I（T-1830）整批 defer |
| **1.1.1** | 2026-08-04 | **DOC-05**：§3.0 壳合并后废止矩阵行；与 MAP/TASKS 债务瘦身同步 |
| **1.1.0** | 2026-07-18 | **T-1890-10**：用户签字解冻；§11 全勾；**可恢复 feature Phase** |
