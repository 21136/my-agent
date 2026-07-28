# Phase 18 细粒度任务清单（STABILIZATION-TASKS）

> 版本 **0.1.0** · 2026-07-15  
> 父文档：[STABILIZATION.md](./STABILIZATION.md) v0.3 · [TASKS.md](./TASKS.md) §Phase 18  
> **用法**：按 ID 顺序执行；每项 **验收** 列必须可勾选；失败须开 `BUGS.md` 并挂 smoke/IT ID。  
> **记录**：P0/P1 手工结果写入 [stabilization-log.md](./stabilization-log.md)。

**ID 规则**：`T-18MCC-NN` — `M`=里程碑 `0`～`3`，`CC`=类别两位码，`NN`=序号。

| 类别码 | 含义 |
|--------|------|
| 00 | 文档评审与定稿 |
| 01 | P0 手工 smoke |
| 02 | P1 手工 smoke |
| 03 | Gate 自动化测试（实现） |
| 04 | Gate runner 与 CI 接线 |
| 05 | 代码修复（可观测性/数据/平台） |
| 06 | 文档交付（DOC-xx） |
| 07 | 审计（parity / 协议漂移 / 测试隔离） |
| 08 | Bug 清册与开放项闭环 |
| 09 | 放行评审 |

---

## M0 — 文档定稿（T-1800-xx）

| ID | 任务 | 交付物 | 验收（全勾才算 done） | 依赖 | 状态 |
|----|------|--------|----------------------|------|------|
| T-1800-01 | 通读 `STABILIZATION.md` v0.3 §0～§2 | 评审备注（可贴 issue/聊天） | 团队对「冻结范围」无歧义 | — | **done** |
| T-1800-02 | 评审 §3.1 壳与会话线档位 | `STABILIZATION.md` 定稿或备注 | 每一行 P0/P1/P2/defer 已确认 | T-1800-01 | **done** |
| T-1800-03 | 评审 §3.2 协议与路由档位 | 同上 | 同上 | T-1800-01 | **done** |
| T-1800-04 | 评审 §3.3～3.6 档位 | 同上 | 同上 | T-1800-01 | **done** |
| T-1800-05 | 评审 §3.8～3.11 平台韧性档位 | 同上 | 含 sidecar 日志 P0 无异议 | T-1800-01 | **done** |
| T-1800-06 | 将 `STABILIZATION.md` 标为 **v1.0 定稿** | 版本历史 §13 | 状态从 draft → 定稿 | T-1800-02～05 | **done** |
| T-1800-07 | 写入 DOC-03「done 定义」到 `TASKS.md` 前言 | `TASKS.md` | §9.2 四条 checklist 可见 | T-1800-06 | **done** |
| T-1800-08 | 写入 DOC-04「新 Phase 准入」到 `TASKS.md` | `TASKS.md` | 新 Phase 须附 §3 矩阵行 + S/IT id | T-1800-06 | **done** |
| T-1800-09 | 确认 `RUNTIME.md` §7.2 模块契约完整 | `RUNTIME.md` | 与 STABILIZATION §7 表一致 | T-1803 | **done** |
| T-1800-10 | 更新 `DESKTOP.md` §5.3 指向 v1.0 smoke 分档 | `DESKTOP.md` | 链到 §5.1/5.2 | T-1800-06 | **done** |

---

## M1-A — P0 手工 smoke · 第 1 轮（T-1801-xx）· **完成**

> **续接（2026-07-18）**：**T-1890-10 done** · **M3 完结** · Phase 18 **已解冻** · 见 [`stabilization-log.md`](./stabilization-log.md) · [`MAP.md`](./MAP.md) §2.1

> 环境：冷启动 `start-desktop.bat`；每项失败 → `BUGS.md` + 阻塞同轮后续可选。

| ID | 任务 | 步骤摘要 | 验收 | 映射 | 状态 |
|----|------|----------|------|------|------|
| T-1801-01 | **S-01** sidecar 启动 | 完全退出 → 启动桌面 | 无 ImportError/NameError；顶栏 WS 就绪 | S-01 | **done** |
| T-1801-02 | **S-02** grow 基础回合 | grow 壳发「1+1」 | `turn.end` ok；输入框可继续发 | S-02 | **done** |
| T-1801-03 | **S-03** 新会话 | 聊天发 `新会话` | ≤3s 回「就绪」；非永久「处理中…」 | S-03 | **done** |
| T-1801-04 | **S-04** grow confirm | 触发小文件 `write_text` → 点同意 | 60s 内 `confirm.done`+`tool.end`+`turn.end` | S-04 | **done** |
| T-1801-05 | **S-05** grow Stop | 长回合（或大上下文）点 Stop | ≤3s `turn.end` `cancelled`；可输入 | S-05 | **done** |
| T-1801-06 | **S-06** project 新建 | `新会话` → `项目 新建 stab-r1-demo` | `workspace/stab-r1-demo/` 三件套；顶栏「计划待确认」 | S-06 | **done** |
| T-1801-07 | **S-07** project 出计划 | 「填 PROJECT.md 和 TASKS.md」 | 助手可写三件套；无假「整轮已拦截」 | S-07 | **done** |
| T-1801-08 | **S-08** project 确认 | 侧栏「确认开工」 | `confirmed`；顶栏 `n/m` | S-08 | **done** |
| T-1801-09 | **S-09** project 切换 | 建 `stab-r1-b` → 侧栏切到 b | 无 ImportError；history **替换** | S-09 | **done** |
| T-1801-10 | **S-10** 壳切换 | project → grow → project | 各壳聊天区不串线 | S-10 | **done** |
| T-1801-11 | **S-12** 真退出 | 托盘退出 | 任务管理器无残留 `python` sidecar | S-12 | **done** |
| T-1801-12 | **S-13** daily 基础 | 切 daily · 问答 · 一次小写 confirm | `turn.end` ok；confirm 不卡 | S-13 | **done** |
| T-1801-13 | **S-14** pet | 伴侶发消息 → 工作台 → 回伴侶 | 无刷屏 traceback；可续聊 | S-14 | **done** |
| T-1801-14 | **S-16** grow↔daily | 各壳聊一句后互切 | 聊天区随壳切换，不串 | S-16 | **done** |
| T-1801-15 | **S-17** 忙时退出 | 回合中点关窗/退出 | 确认框；确认后 sidecar 清理 | S-17 | **done** |
| T-1801-16 | **S-48** LLM 异常 | 临时坏 API key 发消息 | UI 可读错误；不假死；恢复 key 后可续 | S-48 | **done** |
| T-1801-17 | 记入 **P0 第 1 轮** | `stabilization-log.md` P0 表 | 上表 16 项均有 pass/fail+日期 | T-1801-01～16 | **done** |

---

## M1-B — P0 手工 smoke · 第 2、3 轮（T-1802-xx）· **完成**

> **续接（2026-07-16）**：M1-B 全 done · 下一批 **M1-C** · **T-1805-01**

| ID | 任务 | 验收 | 依赖 | 状态 |
|----|------|------|------|------|
| T-1802-01 | P0 第 2 轮：重复 T-1801-01～16 | log 表第 2 行全填 | T-1801-17 | **done** |
| T-1802-02 | P0 第 3 轮：重复 T-1801-01～16 | log 表第 3 行全填；**16 项均 pass** | T-1802-01 | **done** |
| T-1802-03 | P0 三轮中有 fail 的项开 BUG | `BUGS.md` | 每条 fail 有 BUG ID 或已修后重跑 pass | T-1802-02 | **done** |

---

## M1-C — sidecar 日志落盘（T-1805-xx · P0 基础设施）· **完成**

> **续接（2026-07-17）**：**M1-G done**（T-1807-01～04）· 下一批 **M1-H** · **T-1808-01**

| ID | 任务 | 交付物 | 验收 | 依赖 | 状态 |
|----|------|--------|------|------|------|
| T-1805-01 | 定日志路径与命名 | `data/logs/sidecar-YYYYMMDD.log` 或滚动单文件 | 写入 `RUNTIME.md` 或 `DESKTOP.md` §运维 | T-1800-06 | **done** |
| T-1805-02 | `server.py` 启动时挂载 FileHandler | 代码 | 启动后磁盘有日志文件 | T-1805-01 | **done** |
| T-1805-03 | 未捕获异常写入日志（`_run_line` except） | 代码 | 故意抛错可在日志搜到 traceback | T-1805-02 | **done** |
| T-1805-04 | WS `error` 事件与日志双写一致 | 代码 | 同一次错误 UI+日志均有 | T-1805-03 | **done** |
| T-1805-05 | 简单轮转或单文件大小上限 | 代码或 DOC-07 | 不会无限涨爆盘（≥10MB 轮转或文档化手动删） | T-1805-02 | **done** |
| T-1805-06 | 手工：故意杀 sidecar 后查日志 | 记录 | 能定位最后一次异常 | T-1805-04 | **done** |
| T-1805-07 | IT-58：自动化断言日志文件创建 | `tests/test_sidecar_logging.py` | unittest 绿 | T-1805-04 | **done** |

---

## M1-D — Gate 自动化 · project / switch（T-1803-xx）· **完成**

| ID | 任务 | 交付物 | 验收 | 依赖 | 状态 |
|----|------|--------|------|------|------|
| T-1803-01 | `test_project_lifecycle.py`：`项目 新建` | 测试文件 | 创建 workspace 目录+三件套 | T-1800-06 | **done** |
| T-1803-02 | 同上：`项目 确认` 状态迁移 | 测试 | `draft`→`confirmed` | T-1803-01 | **done** |
| T-1803-03 | 同上：`plan_allows_code_writes` 门 | 测试 | draft 拒 run_python；confirmed 允许（mock） | T-1803-02 | **done** |
| T-1803-04 | `test_project_switch.py`：`perform_project_switch` 事件类型 | 测试 | 含 `project.switch.done` | T-1803-01 | **done** |
| T-1803-05 | 同上：`session_replaced` 时 memory←context | 测试 | 事件含 `session.memory`+`session.history`；另断言 `emit_session_state` 连接/刷新载荷完整（history+memory+banner，IT-06 后半） | T-1803-04 | **done** |
| T-1803-06 | `test_module_contracts.py`：import 扫描 | 测试 | `session_memory_event` 不可从 `session` 导入 | T-1800-09 | **done** |
| T-1803-07 | 同上：`project_api` 懒导入路径 | 测试 | 切换分支 import 正确 | T-1803-06 | **done** |

---

## M1-E — Gate 自动化 · confirm / shell / cancel（T-1804-xx）· **完成**

> **续接（2026-07-17）**：**M1-G done**（T-1807-01～04）· 下一批 **M1-H** · **T-1808-01**

| ID | 任务 | 交付物 | 验收 | 依赖 | 状态 |
|----|------|--------|------|------|------|
| T-1804-01 | 扩 `test_confirm_pipeline.py`：错 request_id | 测试 | 不空转；`confirm.done` | T-1308 | **done** |
| T-1804-02 | 同上：超时路径 | 测试 | `CONFIRM_TIMEOUT_SEC` 后 `confirm.done` | T-1804-01 | **done** |
| T-1804-03 | 同上：stale 卡 | 测试 | 旧 id 返回 notice | T-1804-01 | **done** |
| T-1804-04 | 扩 `test_cross_session_read.py`：shell 三线 | 测试 | grow/daily/project 不同 `conversation_id` | T-1116 | **done** |
| T-1804-05 | 扩 `test_turn_cancel.py`：cancel 后 turn.end | 测试 | `finish_reason=cancelled` | T-1407 | **done** |
| T-1804-06 | `test_activity_router.py`：项目信号→project | 新文件 | `compute_activity_route` 用例 | T-1800-06 | **done** |
| T-1804-07 | IT-17：跨会话 read confirm | 测试 | 非当前会话须 confirm | T-1117 | **done** |

---

## M1-F — Gate 自动化 · guards / 数据 / LLM（T-1806-xx）

| ID | 任务 | 交付物 | 验收 | 依赖 | 状态 |
|----|------|--------|------|------|------|
| T-1806-01 | 将 `test_runtime_guards.py` 列入 Gate | `run_stabilization.py` | runner 调用通过 | T-1518 | **done** |
| T-1806-02 | 将 `test_runtime_guards_m1.py` 列入 Gate | runner | 同上 | T-1520 | **done** |
| T-1806-03 | 将 `test_checker_subagent.py` 列入 Gate（子集） | runner | 核心用例绿 | T-1614 | **done** |
| T-1806-04 | IT-42：`repair_orphaned_tool_calls` | 测试或扩展现有 | 残缺 tool_calls 可修复 | BUG-005 | **done** |
| T-1806-05 | IT-51：LLM timeout 链路 | 复用 T-1519 测试 | Gate 绿 | T-1519 | **done** |
| T-1806-06 | IT-60：`sanitize_log_value` 不进 key | 扩 `tools/logging.py` demo | API key 变 `[REDACTED]` | T-110 | **done** |
| T-1806-07 | IT-55：坏 jsonl 行 — **先写失败用例**（记现状） | `test_session_corruption.py` | 记录当前静默行为；修后改断言为「告知」 | T-1800-06 | **done** |
| T-1806-08 | IT-56：坏 `state.json` 不崩 | 同上 | 启动/切换不 traceback | T-1806-07 | **done** |

---

## M1-G — Gate runner（T-1807-xx）· **完成**

| ID | 任务 | 交付物 | 验收 | 依赖 | 状态 |
|----|------|--------|------|------|------|
| T-1807-01 | 创建 `tests/run_stabilization.py` | 文件 | `python tests/run_stabilization.py` exit 0 | T-1803,T-1804 | **done** |
| T-1807-02 | runner 打印分项 PASS/FAIL 摘要 | 输出 | 失败时非零 exit | T-1807-01 | **done** |
| T-1807-03 | runner 文档写入 `STABILIZATION.md` §6 | 文档 | 命令与 Gate 列表一致 | T-1807-02 | **done** |
| T-1807-04 | 本地跑 Gate 全绿 | 记录 | 截图或终端日志存档 | T-1807-02 | **done** |

---

## M1-H — CLI parity 审计（T-1808-xx）

| ID | 任务 | 交付物 | 验收 | 依赖 | 状态 |
|----|------|--------|------|------|------|
| T-1808-01 | 列出元命令全集（main `handle_line`） | `docs/CLI-DESKTOP-PARITY.md` 或 STABILIZATION 附录 | ≥15 条命令 | T-1800-06 | **done** |
| T-1808-02 | 逐条标注桌面等价路径 | 同上表 | 每行：WS 类型 / 侧栏 / N/A | T-1808-01 | **done** |
| T-1808-03 | 标出 N/A 项的用户绕行文案 | 同上 | 无「静默不支持」 | T-1808-02 | **done** |
| T-1808-04 | IT-38：parity 表关键项自动化 | `test_cli_desktop_parity.py` | 至少 `新会话`/`项目 新建`/`压缩` | T-1808-02 | **done** |
| T-1808-05 | `command` WS vs `user.message` 元命令 | 手工或 IT-11 | 行为一致 | T-1808-02 | **done** |

---

## M2-A — P1 手工 smoke · 壳 / confirm（T-1820-xx）

| ID | 任务 | 映射 | 验收要点 | 状态 |
|----|------|------|----------|------|
| T-1820-01 | **S-11** project 拖放 | S-11 | staged 或合意错误 | **done** |
| T-1820-02 | **S-15** grow proposals | S-15 | accept/reject 不卡 | **done** |
| T-1820-03 | **S-18** Electron 闪退 | S-18 | Vite 仍存活 | **done** |
| T-1820-04 | **S-19** plan.request 卡 | S-19 | 与侧栏确认等价 | **done** |
| T-1820-05 | **S-20** 跨项目确认卡 | S-20 | 取消不换 | **done** |
| T-1820-06 | **S-21** project.verify | S-21 | confirmed 后验收命令 | **done** |
| T-1820-07 | **S-22** 断线重连 | S-22 | banner/history 一致 | **done** |
| T-1820-08 | **S-23** daily/pet confirm | S-23 | 同 S-04 语义 | **done** |
| T-1820-09 | **S-24** write_evolve 二次 confirm | S-24 | BUG-008 路径不空转 | **done** |
| T-1820-10 | **S-25** confirm 中 Stop | S-25 | cancelled | **done** |
| T-1820-11 | **S-26** confirm 90s 超时 | S-26 | confirm.done | **done** |
| T-1820-12 | **S-27** host 写 confirm | S-27 | 绝对路径标签 | **done** |
| T-1820-13 | **S-28** 四壳 Stop | S-28 | project/daily/pet 同 S-05 | **done** |
| T-1820-14 | **S-29** recall | S-29 | 不乱调工具 | **done** |
| T-1820-15 | **S-30** 压缩 | S-30 | notice 可见 | **done** |
| T-1820-16 | **S-31** memory 顶栏 | S-31 | 条数合理 | **done** |
| T-1820-17 | P1 本批记入 log | log P1 表 | 有日期 | T-1820-01～16 | **done** |

---

## M2-B — P1 手工 smoke · project / host / grow（T-1821-xx）

| ID | 任务 | 映射 | 验收要点 | 状态 |
|----|------|------|----------|------|
| T-1821-01 | **S-33** draft 拒写码 | S-33 | run_python/源码写被拒 | **done** |
| T-1821-02 | **S-34** plan_dirty 再确认 | S-34 | 改 Phase 后须再确认 | **done** |
| T-1821-03 | **S-35** grow 造工具 | S-35 | write_evolve 最小路径 | **done** |
| T-1821-04 | **S-36** 自动 checker notice | S-36 | 顶栏/notice 有 verdict | **done** |
| T-1821-05 | **S-37** 手动验收 CLI | S-37 | `验收` 可跑 | **done** |
| T-1821-06 | **S-38** 完成声明门 | S-38 | FAIL 不得「已验收」 | **done** |
| T-1821-07 | **S-39** 托管区向导 | S-39 | 可加 host 目录 | **done** |
| T-1821-08 | **S-40** host 只读+denylist | S-40 | `.ssh` 类拒绝 | **done** |
| T-1821-09 | **S-41** grow 拖放 | S-41 | `_drops` | **done** |
| T-1821-10 | **S-42** daily/pet 拖放 | S-42 | 不崩 | **done** |
| T-1821-11 | **S-43** 忙时 project.switch | S-43 | 被拒或说明 | **done** |
| T-1821-12 | **S-44** project 壳新会话 | S-44 | 不串旧聊天 | **done** |
| T-1821-13 | **S-45** 壳锁定 | S-45 | 忽略 auto route | **done** |
| T-1821-14 | **S-46** CLI+桌面双开 | S-46 | lock 提示清晰 | **done** |
| T-1821-15 | **S-49** 断网发消息 | S-49 | 可重试 | **done** |
| T-1821-16 | **S-50** CLI 中文/编码 | S-50 | PowerShell 无乱码 | **done** |
| T-1821-17 | **S-51** fresh bootstrap | S-51 | 新 clone 可跑 | **done** |
| T-1821-18 | **S-52** 双实例启动 | S-52 | 端口冲突有提示 | **done** |
| T-1821-19 | P1 全表记入 log | log | 全行或 BUG+绕行 | T-1821-01～18 | **done** |

---

## M2-C — P1 手工 smoke · P2 抽样（T-1822-xx）

| ID | 任务 | 映射 | 验收 | 状态 |
|----|------|------|------|------|
| T-1822-01 | **S-32** 只聊/动手 | S-32 | 工具预算差异可感知 | **done** |
| T-1822-02 | **S-47** govern 占位 | S-47 | 不崩；可回 grow | **done** |
| T-1822-03 | P2 失败项记入 backlog | `TASKS` defer 或 BUG P2 | 不阻塞放行 | T-1822-01～02 | **done** |

---

## M2-D — 协议漂移审计（T-1813-xx）

| ID | 任务 | 交付物 | 验收 | 状态 |
|----|------|--------|------|------|
| T-1813-01 | 从 `server.py` 提取全部 WS `type` 入站列表 | 附录表 A | 与 `DESKTOP.md` §5  diff | **done** |
| T-1813-02 | 从 `_emit`/`bridge.emit` 提取出站 `type` 列表 | 附录表 B | 与前端 `ws.ts` case  diff | **done** |
| T-1813-03 | 标出「文档有/代码无」「代码有/文档无」 | 漂移清单 | 每条有处置：修 doc / 修代码 / defer | **done** |
| T-1813-04 | 更新 `DESKTOP.md` §5.3 开放勾选（Phase 15） | `DESKTOP.md` | 与实现一致 | T-1813-03 | **done** |

---

## M2-E — 代码修复 · 数据韧性（T-1823-xx）

| ID | 任务 | 交付物 | 验收 | 依赖 | 状态 |
|----|------|--------|------|------|------|
| T-1823-01 | 设计：坏 jsonl 行用户可见 notice 方案 | 备注或 STABILIZATION 补丁 | 不静默丢消息 | T-1806-07 | **done** |
| T-1823-02 | 实现：`load_messages` 跳过行时 `turn.notice` 或 banner | `session.py` | IT-55 绿 | T-1823-01 | **done** |
| T-1823-03 | 设计：坏 meta.json 告知方案 | 备注 | 用户知绑定丢失 | T-1806-07 | **done** |
| T-1823-04 | 实现：坏 meta 加载时 WS notice | `session.py` / `server.py` | IT-55 绿 | T-1823-03 | **done** |
| T-1823-05 | 坏 `state.json` 降级为 `{}` 并 notice | 代码 | IT-56 绿 | T-1806-08 | **done** |
| T-1823-06 | 编写 DOC-05 schema 兼容表 | `RUNTIME.md` 或 `MEMORY.md` § | 旧会话字段默认值 | T-1823-05 | **done** |

---

## M2-F — 代码修复 · 平台/编码（T-1824-xx）

| ID | 任务 | 交付物 | 验收 | 依赖 | 状态 |
|----|------|--------|------|------|------|
| T-1824-01 | 调查 Windows 乱码复现路径 | BUG 或备注 | 记录 code page / 输出点 | S-50 | **done** |
| T-1824-02 | CLI 输出 UTF-8 策略（chcp 65001 或 PYTHONIOENCODING） | `start.bat` / 文档 | S-50 pass | T-1824-01 | **done** |
| T-1824-03 | sidecar 子进程 UTF-8 | `electron`  spawn 或 `server.py` | 日志中文可读 | T-1805-02 | **done** |
| T-1824-04 | IT-62：审计测试用临时目录 | 清单 | 列出现有污染 `data/` 的测试 | T-1800-06 | **done** |
| T-1824-05 | 修复最高危测试隔离（优先 demo 类） | 代码 | 单测后 `data/` 无 `_demo` 残留 | T-1824-04 | **done** |
| T-1824-06 | IT-61：中文文件名拖放 | 测试或手工 S 扩 | 合意错误或成功 | T-1820-01 | **done** |

---

## M2-G — 文档交付 DOC-01～09（T-1806-xx）

| ID | 任务 | 交付物 | 验收 | 状态 |
|----|------|--------|------|------|
| T-1806-doc-01 | **DOC-01** pet→daily 映射 | `PET-SHELL.md` 或 `DESKTOP.md` § | 读者知 pet 用 daily 会话线 | **done** |
| T-1806-doc-02 | **DOC-02** CLI parity 定稿 | `CLI-DESKTOP-PARITY.md` | 与 T-1808 表一致 | T-1808-03 | **done** |
| T-1806-doc-03 | **DOC-03** done 定义 | `TASKS.md` | T-1800-07 | T-1800-07 | **done** |
| T-1806-doc-04 | **DOC-04** Phase 准入 | `TASKS.md` | T-1800-08 | T-1800-08 | **done** |
| T-1806-doc-05 | **DOC-05** schema 兼容 | `RUNTIME.md` | T-1823-06 | T-1823-06 | **done** |
| T-1806-doc-06 | **DOC-06** data 备份建议 | `PROJECT.md` 或 `STABILIZATION` § | 用户知 `data/` 不在 git | **done** |
| T-1806-doc-07 | **DOC-07** 资源增长与清理 | 同上 | evolve_log/sessions/pkl 清理策略 | **done** |
| T-1806-doc-08 | **DOC-08** 编码策略 | `DESKTOP.md` 或 `start.bat` 注释 | 与 T-1824-02 一致 | T-1824-02 | **done** |
| T-1806-doc-09 | **DOC-09** bootstrap 清单 | `README` 或 `PROJECT.md` | clone→pip→npm→首启步骤 | **done** |

---

## M2-H — 开放项与 BUG 闭环（T-1808-xx）

| ID | 任务 | 验收 | 状态 |
|----|------|------|------|
| T-1808-bug-01 | 清 BUG-014「待验收」 | S-05/S-26/S-28 有 pass 记录 → fixed；否则 open | **done** |
| T-1808-bug-02 | 清 Phase 16 T-1517「待手工验收」 | 并入 S-28 pass | **done** |
| T-1808-bug-03 | 清 Phase 17 checker notice「待巩固」 | S-36～38 pass | **done** |
| T-1808-bug-04 | 扫描 `BUGS.md` 无 open P0 | 索引表 | **done** |
| T-1808-bug-05 | 扫描 open P1：每条有修复计划或绕行 | 备注 | **done** |
| T-1808-bug-06 | `STABILIZATION.md` §10 开放项逐条勾选 | §10 表 | **done** |

---

## M2-I — 扩展 IT（IT-X · 非 Gate 阻塞）（T-1830-xx）· **defer**

> 放行允许 ≤3 条 defer（非安全类）。**T-1890-08**：本批 **整批 defer**（放行后维护；不计入 §6.2 的 3 条显式 defer 名额——那些是 IT-38/62/59）。每项独立勾选。

| ID | 任务 | IT | 状态 |
|----|------|-----|------|
| T-1830-01 | `session.refresh` 载荷测试 | IT-09 | **defer** |
| T-1830-02 | `project.open` 绑定规则 | IT-12 | **defer** |
| T-1830-03 | `host_scope.*` WS 集成 | IT-13 | **defer** |
| T-1830-04 | 流式事件序 | IT-15 | **defer** |
| T-1830-05 | error 后 UI 可输入 | IT-16 | **defer** |
| T-1830-06 | checker.verdict notice | IT-14 | **defer** |
| T-1830-07 | stall watchdog | IT-18 | **defer** |
| T-1830-08 | Stop 杀 subprocess | IT-19 | **defer** |
| T-1830-09 | history 过滤规则 | IT-43 | **defer** |
| T-1830-10 | host_scope 落盘往返 | IT-44 | **defer** |
| T-1830-11 | interface_lock | IT-39 | **defer** |
| T-1830-12 | bootstrap 连接事件序 | IT-47 | **defer** |
| T-1830-13 | 记录 defer 项（若有） | [stabilization-backlog.md](./stabilization-backlog.md) §索引 + STABILIZATION §11 备注 | **done** |

---

## M3 — 放行评审（T-1890-xx）

| ID | 任务 | 验收（全部勾选） | 依赖 | 状态 |
|----|------|------------------|------|------|
| T-1890-01 | P0 smoke 连续 3 轮全 pass | `stabilization-log.md` | T-1802-02 | **done** |
| T-1890-02 | Gate runner 全绿 | T-1807-04 记录 | T-1807-04 | **done** |
| T-1890-03 | P1 全 pass 或均有 BUG+绕行 | log P1 表 | T-1821-19 | **done** |
| T-1890-04 | sidecar 日志落盘已上线 | T-1805-06 | T-1805-06 | **done** |
| T-1890-05 | DOC-01～09 已交付 | 文件存在 | T-1806-doc-* | **done** |
| T-1890-06 | `BUGS.md` 无裸奔 P0/P1 | T-1808-bug-04/05 | T-1808-bug-* | **done** |
| T-1890-07 | `STABILIZATION.md` 标 **done** | §13 版本 | T-1890-01～06 | **done** |
| T-1890-08 | `TASKS.md` Phase 18 全 task **done** | 本文件 + 索引表 | T-1890-07 | **done** |
| T-1890-09 | `MAP.md` / `project-map.mdc` 解冻说明 | 文案 | T-1890-08 | **done** |
| T-1890-10 | **用户签字**：可恢复 feature Phase | 聊天记录或备注 | T-1890-09 | **done** |

---

## 索引：粗粒度 T-18xx → 细粒度映射

| 粗 ID | 细粒度范围 |
|-------|------------|
| T-1801 | T-1800-01～10 |
| T-1802 | T-1801-01～17, T-1802-01～03 |
| T-1803 | T-1800-09, T-1803-01～07, T-1806-doc-05 |
| T-1804 | T-1803-01～03 |
| T-1805 | T-1803-04～05 |
| T-1806 | T-1804-01～03 |
| T-1807 | T-1808-bug-01～06, T-1820-09～11 |
| T-1808 | T-1808-01～05 |
| T-1809 | T-1808-bug-04～05 |
| T-1810 | T-1807-01～04, T-1806-01～03 |
| T-1811 | T-1801-07, T-1821-01～02 |
| T-1812 | T-1890-01～10 |
| T-1813 | T-1813-01～04 |
| T-1814 | T-1801-12～14, T-1801-14 |
| T-1815 | T-1804-06, T-1821-13, T-1830-01 |
| T-1816 | T-1800-07～08 |
| T-1817 | T-1821-07～08, T-1830-03 |
| T-1818 | T-1820-02, T-1821-03～06, T-1820-09 |
| T-1819 | T-1806-01～04, T-1804-07 |
| T-1820 | T-1801-15, T-1820-07, T-1821-14, T-1830-11 |
| T-1821 | T-1805-01～07 |
| T-1822 | T-1801-16, T-1821-15, T-1806-05 |
| T-1823 | T-1823-01～06, T-1806-07～08 |
| T-1824 | T-1824-01～06, T-1821-16～18 |
| T-1825 | T-1806-doc-06～07, T-1805-05 |

---

## 统计

| 里程碑 | 细粒度 task 数 |
|--------|----------------|
| M0 文档 | 10 |
| M1 P0 smoke + 三轮 | 20 |
| M1 日志 + Gate 实现 + runner + parity | 35 |
| M2 P1 smoke + 审计 + 修复 + DOC + BUG | 58 |
| M2 扩展 IT | 13 |
| M3 放行 | 10 |
| **合计** | **~146** |
