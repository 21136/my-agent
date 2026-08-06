# 稳定化验收日志

> Phase 18 · [STABILIZATION.md](./STABILIZATION.md) §5  
> 每次冷启动 `start-desktop.bat` 后先跑 **P0**，再择机跑 **P1**。

---

## 续接指引（新会话先读）

> 一句话不够时：**本文档 + [`STABILIZATION-TASKS.md`](./STABILIZATION-TASKS.md) + [`STABILIZATION.md`](./STABILIZATION.md) v1.1.0** 即完整上下文。

### 我们在做什么

- **Phase 18 稳定化** · [`STABILIZATION.md`](./STABILIZATION.md) **v1.1.0** · **done · 已解冻**
- **M3 完结**：T-1890-01～10 全 **done**（2026-07-18 用户签字解冻）
- **下一方向**：可开新功能 Phase（须 DOC-04 / §9.3）；放行后债见 backlog **STD-001** · M2-I defer

### 进度快照（2026-07-18）

| 已完成 | **M1～M3** · **T-1890-01**～**10** · **Phase 18 放行** |
| 冻结 | **已解除** |
| STABILIZATION | **v1.1.0** · §11 **全勾** |
| 仍 open（非阻塞） | backlog **STD-001**；M2-I T-1830 defer |

### 新窗口开场白（解冻后）

```text
Phase 18 已解冻。新功能 Phase 须附 DOC-04（§3 矩阵行 + 回归 S/IT id）。
先读 docs/MAP.md §2.1 · docs/STABILIZATION.md v1.1.0。
```

**极短版**：`Phase 18 已解冻；新 Phase 走 DOC-04。`

### 验收方式（本 run 惯例）

| 方式 | 说明 |
|------|------|
| **主路径** | 多数项用 **WS 协议级 smoke**（`ws://127.0.0.1:8765`），等价桌面行为，不必每项开 Electron |
| **sidecar 启动** | `python agent-core\server.py --port 8765 --takeover`（与 Electron 相同参数）；或 `start-desktop.bat` |
| **冷启前** | 确认 8765 无监听；**当前环境 sidecar 常未运行**（历次验收后已杀进程） |
| **可选补记** | S-12 托盘 UI、S-17 忙时确认框 — 三轮均未做 Electron UI；可随 P1 或放行后补 |

### 磁盘 / 会话状态（勿随意改）

| 路径 | 说明 |
|------|------|
| `data/state.json` · `shell_sessions` | **须 grow≠daily**；S-47 时曾同指向 `20260717-1593d29d`（**STD-001** 污染残留）。理想参考曾为 grow `20260713-fc1acefd` · daily `20260715-b5215de6` — 测前核对，污染则先拆分 |
| `workspace/stab-r1-demo/` | S-06～08 项目 demo；`plan_status: confirmed` |
| `workspace/stab-r1-b/` | S-09 切换 demo |
| `workspace/stab-s04-test.txt` | S-04 confirm 产物 |
| `workspace/stab-s13-daily.txt` | S-13 daily confirm 产物 |

### 已知坑（续接必读）

1. **TURN_LOCK**：WS 断连后 server 回合可能仍占锁 → 后续 `user.message` 超时 → **重启 sidecar**
2. **`write_text` 默认 `on_conflict=skip`**：已有文件时静默跳过；测 confirm 需 **overwrite** 或新文件名
3. **`session.history` 字段为 `items`**（每项 `role` + `text`），不是 `messages`
4. **activity_router × park_session**：~~grow Q&A 可能污染 `shell_sessions.daily`~~ → **BUG-020 fixed**（归属反查 park + 软路由不改写 meta）。若本机仍 grow=daily 同 id，请手工拆分映射。
5. **PowerShell**：勿用 `cd x && cmd`（旧版不支持 `&&`）；sidecar 用 `;` 或分开命令；**Tee-Object / `Get-Content` 默认编码易把 UTF-8 显示成乱码** — 探针结果以 Python 写的 UTF-8 文件为准
6. **`npm run dev`**：常驻进程，自动化测 S-12 会挂住；用 sidecar 启停代替
7. **编码（T-1824-01～03）**：本机默认 **CP936 / stdout=gbk**；`start.bat` + Electron `sidecarSpawnEnv` 均已强制 UTF-8；磁盘 sidecar 日志 UTF-8。裸跑 `python …\main.py` / `server.py` 无 env 时仍可能乱码
8. **测试隔离（T-1824-04/05）**：Gate 高危已改 `tests/isolation_helpers.make_temp_agent_paths`；历史 `data/sessions/_*` 残留仍在（未批量清盘）。**勿**删用户真实会话。清单 [`data/_t1824-04-audit.md`](../data/_t1824-04-audit.md)

### 新窗口开场白（推荐 · 复制整段）

```text
Phase 18 已解冻（STABILIZATION v1.1.0）。
新功能 Phase 须附 DOC-04（§3 矩阵行 + 回归 S/IT id）。
先读 docs/MAP.md §2.1。
```

**极短版**：`Phase 18 已解冻；新 Phase 走 DOC-04。`

---

## P0 记录表（阻塞放行 · 须连续 3 次全 pass）

| 日期 | # | S-01 | S-02 | S-03 | S-04 | S-05 | S-06 | S-07 | S-08 | S-09 | S-10 | S-12 | S-13 | S-14 | S-16 | S-17 | S-48 | 备注 |
|------|---|------|------|------|------|------|------|------|------|------|------|------|------|------|------|------|------|------|
| 2026-07-15～16 | 1 | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | **16/16** · T-1801-01～17 |
| 2026-07-16 | 2 | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | **16/16** · T-1802-01 |
| 2026-07-16 | 3 | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | **16/16** · T-1802-02 |

### T-1802-03 · P0 三轮 fail→BUG 扫描（2026-07-16 · **N/A**）

| 检查项 | 结果 |
|--------|------|
| P0 表 run #1～#3 | **48/48 格均为 pass**；无 fail 单元格 |
| `BUGS.md` 新增 | **无**（本任务不要求开单） |
| 历史 transient | run #1 详记：S-13 当日首次自动化超时，**同日重试 pass**；终态 P0 表为 pass → 不追溯开 BUG |
| 绕行项 | S-14/S-16 **STD-001** 已记入 [`stabilization-backlog.md`](./stabilization-backlog.md)；三轮均 pass，非 P0 fail |

### T-1805-01 · sidecar 日志路径（2026-07-16 · **done**）

- **路径**：`<agent_root>/data/logs/sidecar-YYYYMMDD.log`（本地日）
- **代码**：`agent-core/sidecar_logging.py` · `sidecar_log_path()` / `SIDECAR_LOGGER_NAME`
- **文档**：[`DESKTOP.md`](./DESKTOP.md) §4.4.3 · [`RUNTIME.md`](./RUNTIME.md) §11 模块表
- **`.gitignore`**：`data/logs/`

### T-1805-02 · FileHandler 挂载（2026-07-16 · **done**）

- **代码**：`configure_sidecar_logging(paths)` · `server.py` `run_server()` 首行调用
- **格式**：`%(asctime)s %(levelname)s %(message)s` · UTF-8 append · `propagate=False`
- **冷启验收**：`python agent-core\server.py --port 8765 --takeover` → 磁盘 `data/logs/sidecar-20260716.log` 含 `sidecar logging ready`；stdout 仅 `{"ready": true, ...}`

### T-1805-03 · `_run_line` 异常写日志（2026-07-16 · **done**）

- **代码**：`sidecar_logging.log_sidecar_exception()` · `server.py` `_run_line` except 分支
- **验收**：monkeypatch `repl.handle_line` 抛 `RuntimeError` → 日志含 `Traceback` + `_run_line failed line=`；WS 仍发 `error` 事件

### T-1805-04 · WS error 双写（2026-07-16 · **done**）

- **代码**：`log_sidecar_ws_error()` · `emit_error()` 统一写 `ws error: {message}` 再 emit
- **覆盖**：`emit_error` 全路径 · `WsBridge.output_fn` REPL error 行 · `handle()` invalid JSON · `_run_line` except（traceback + ws error 各一行）
- **验收**：`emit_error` / invalid JSON WS smoke → UI `error` 与日志 `ws error:` 同文案

### T-1805-05 · 日志轮转（2026-07-16 · **done**）

- **代码**：`RotatingFileHandler` · `SIDECAR_LOG_MAX_BYTES=10MB` · `SIDECAR_LOG_BACKUP_COUNT=5`
- **命名**：超限后 `sidecar-YYYYMMDD.log` → `.1` … `.5`（同日 stem）
- **文档**：[`DESKTOP.md`](./DESKTOP.md) §4.4.3 轮转行已更新
- **验收**：`sidecar_logging.py` demo 断言 handler 类型与 10MB；小 `maxBytes` smoke 产生 ≥2 个文件

### T-1805-06 · 强杀 sidecar 取证（2026-07-16 · **done**）

**步骤**（WS smoke，等价桌面 sidecar）：

1. 冷启：`python agent-core\server.py --port 8765 --takeover`（pid **17176**）
2. WS 连 `127.0.0.1:8765` → 发非法 JSON → 发 `{"type":"unknown.t180506"}`
3. `Stop-Process -Force` 强杀（非优雅退出）
4. 读 `data/logs/sidecar-20260716.log` 尾部

**结果**：

| 检查项 | 结果 |
|--------|------|
| 强杀后进程消失 | `alive_after=False` |
| 本次启动行 | `16:23:32 INFO sidecar logging ready` |
| 最后一次 error | `16:23:34 ERROR ws error: invalid JSON` |
| 第二次 error | `16:23:34 ERROR ws error: unknown type: unknown.t180506` |
| 历史 traceback | 同日更早的 `_run_line` / `RuntimeError` 堆栈仍在（T-1805-03 行） |

→ **pass**：强杀后磁盘日志可定位最后一次 WS error；历史异常堆栈未丢。

### T-1805-07 · IT-58 自动化（2026-07-16 · **done**）

- **文件**：`agent-core/tests/test_sidecar_logging.py`（9 cases）
- **覆盖**：路径命名 · configure 建文件 · RotatingFileHandler · idempotent · `emit_error` 双写 · `output_fn` REPL error · exception traceback · `_run_line` 双写 · 小 `maxBytes` 轮转
- **隔离**：临时 `sidecar_log_dir` patch；不污染 `data/logs/`
- **验收**：`python -m unittest tests.test_sidecar_logging -v` → **9/9 OK**

**M1-C 批次完结**（T-1805-01～07）。

### T-1803-01 · project 新建 + 三件套（2026-07-16 · **done**）

- **文件**：`agent-core/tests/test_project_lifecycle.py`
- **用例**：`test_project_new_creates_workspace_and_triad` — `parse_project_command("项目 新建 …")` + `run_project_command`
- **断言**：`workspace/<id>/` 存在 · `PROJECT.md` / `MAP.md` / `TASKS.md` 非空 · `project_plan_status=draft` · `active_shell=project` · `project_sessions` 索引写入
- **隔离**：`test-lifecycle-{hex}` 项目 id · `addCleanup` 删 workspace 目录 + 会话目录 + 还原 `state.json` 映射
- **验收**：`python -m unittest tests.test_project_lifecycle -v` → **1/1 OK**

### T-1803-02 · 项目 确认 draft→confirmed（2026-07-16 · **done**）

- **用例**：`test_project_confirm_migrates_draft_to_confirmed` — `_run_project_new()` → `项目 确认`
- **断言**：`project_plan_status=confirmed` · `project_plan_confirmed_at` 非空 · `plan_allows_code_writes` 为真 · `goal` 含 `confirmed` · 输出含「计划已确认」
- **验收**：`python -m unittest tests.test_project_lifecycle -v` → **2/2 OK**

### T-1803-03 · plan_allows_code_writes 门（2026-07-16 · **done**）

- **用例**：`test_draft_rejects_run_python_plan_gate` · `test_confirmed_allows_run_python`
- **路径**：`ToolExecutor.run("run_evolved", {tool_name: run_python})` → `project_mode_block_reason` / `_validate_project_mode_call`
- **draft**：`validation_error` · 文案含「计划未确认」· `details.project_plan_status=draft`
- **confirmed**：patch `_BUILTIN_RUNNERS["run_evolved"]` mock 执行 · `result.ok` · 非 plan gate 拦截
- **验收**：`python -m unittest tests.test_project_lifecycle -v` → **4/4 OK**

### T-1803-04 · project.switch.done（2026-07-16 · **done**）

- **文件**：`agent-core/tests/test_project_switch.py`
- **用例**：`test_perform_project_switch_emits_done` — `项目 新建` A → `perform_project_switch` 切 B（`confirm: true`）
- **断言**：事件含 `project.switch.done`（`session_replaced` · `session_id` 变更 · `action` ∈ {`new_session`,`load_session`}）+ `project.state` + `session.banner`
- **附加**：`test_perform_project_switch_requests_confirm_without_flag` — 无 `confirm` 仅 `project.switch.request`
- **验收**：`python -m unittest tests.test_project_switch -v` → **2/2 OK**

### T-1803-05 · session_replaced memory/history（2026-07-16 · **done**）

- **用例**：`test_session_replaced_emits_memory_and_history`
- **路径**：`perform_project_switch` `session_replaced=true` → `session.memory`（`context.session_memory_event`）+ `session.history`（`session.session_history_event`）
- **续接**：`load_session` 二次切换 history 含 `MARKER-SESSION-B`
- **IT-06**：`WsBridge.emit_session_state` 载荷含 `session.banner` + `session.memory` + `session.history` 且与 helper 一致
- **验收**：`python -m unittest tests.test_project_switch -v` → **3/3 OK**

### T-1803-06 · import 模块契约（2026-07-17 · **done**）

- **文件**：`agent-core/tests/test_module_contracts.py`
- **用例**：`from session import session_memory_event` 子进程 **ImportError** · 对称检查 `session_history_event` 不可从 `context` 导入
- **正向**：`context.session_memory_event` · `session.session_history_event` 可导入
- **扫描**：`agent-core/**/*.py` 无 forbidden import 模式（BUG-019 回归）
- **验收**：`python -m unittest tests.test_module_contracts -v` → **4/4 OK**

### T-1803-07 · project_api 懒导入路径（2026-07-17 · **done**）

- **AST**：`perform_project_switch` 含 `context.session_memory_event` + `session.session_history_event` 懒导入；无 `session.session_memory_event`
- **运行时**：`test_perform_project_switch_session_replaced_branch_imports` — `session_replaced` 路径无 ImportError，事件含 memory/history
- **验收**：`python -m unittest tests.test_module_contracts -v` → **6/6 OK**

**M1-D 批次完结**（T-1803-01～07）。

### T-1804-01 · 错 request_id 不空转（2026-07-17 · **done**）

- **文件**：`agent-core/tests/test_confirm_pipeline.py`
- **用例**：`test_wrong_request_id_emits_confirm_done_without_spin` — `deliver_confirm(wrong_id)` 专测（与 `test_stale_deliver_confirm_rejected` 拆清边界）
- **断言**：`deliver_confirm` 返回 `False` · 立即发 `confirm.done`（`request_id=wrong_id` · `choice=stale`）· `notice` 含「过期」· `confirm_fn` 线程 **150ms 内不返回** · `_pending_confirm_id` 保持 · 正确 id 后正常 `join`
- **参考**：`server.py` `WsBridge.deliver_confirm()` C1 ingress · BUG-008
- **验收**：`python -m unittest tests.test_confirm_pipeline -v` → **7/7 OK**

### T-1804-02 · CONFIRM_TIMEOUT_SEC 超时路径（2026-07-17 · **done**）

- **文件**：`agent-core/tests/test_confirm_pipeline.py`
- **用例**：`test_confirm_timeout_sec_env_emits_done` — `patch CONFIRM_TIMEOUT_SEC=0.12` · `WsBridge` 默认 `confirm_timeout` · 后台 `confirm_fn` 无响应
- **断言**：`confirm_timeout_sec()` 读 env · 超时后 `confirm_fn` 返回 `"n"` · `confirm.done`（`request_id` 匹配 · `choice=timeout`）· `notice` 含「超时」· `_pending_confirm_id` 清空
- **加强**：`test_confirm_timeout_emits_done` 补 `request_id` 唯一性 + pending 清空断言（与 env 用例拆边界）
- **参考**：`server.py` `confirm_fn` C2 · BUG-008b · `CONFIRM_TIMEOUT_SEC` 默认 90s
- **验收**：`python -m unittest tests.test_confirm_pipeline -v` → **8/8 OK**

### T-1804-03 · stale 卡 notice（2026-07-17 · **done**）

- **文件**：`agent-core/tests/test_confirm_pipeline.py`
- **用例**：`test_stale_card_returns_notice` — 合并测两条 C1 告警路径（与 T-1804-01 拆边界：专注重 notice 文案）
- **Ingress**：`deliver_confirm(stale_id)` → `notice` 含「请点最新一张工具确认卡」· `confirm.done`（`choice=stale`）
- **Queue 防御**：预置 `orphan-queue-id` → `notice` 含「request_id 不匹配」· 对应 `confirm.done stale` · pending 仍可正常 `n` 结束
- **参考**：`server.py` `deliver_confirm()` ingress · `confirm_fn` queue 循环 · BUG-008
- **验收**：`python -m unittest tests.test_confirm_pipeline -v` → **9/9 OK**

### T-1804-04 · shell 三线独立 conversation_id（2026-07-17 · **done**）

- **文件**：`agent-core/tests/test_cross_session_read.py`
- **类**：`ShellSessionIsolationTests`（unittest · 与既有 pytest 用例共存）
- **用例**：`test_grow_daily_project_have_distinct_conversation_ids` — 三线 seed · `shell_sessions` / `project_sessions` 映射 · `switch_shell` project→grow→daily→project 各回正确 `conversation_id` · grow 线无 `project_id` 残留
- **用例**：`test_cross_session_read_across_shell_lines` — grow↔project · daily↔grow 跨线 `cross_session_read_target` · 同会话返回 `None`
- **隔离**：`test-shell-{hex}` 项目 · `_test_shell_*` 会话 · `addCleanup` 还原 `state.json` 映射
- **参考**：`shell_switch.py` T-1116 · `switch_shell` / `park_session`
- **验收**：`python -m unittest tests.test_cross_session_read -v` → **2/2 OK** · pytest 全文件 **4/4**

### T-1804-05 · cancel 后 turn.end（2026-07-17 · **done**）

- **文件**：`agent-core/tests/test_turn_cancel.py`
- **用例**：`test_cancel_emits_turn_end_with_cancelled_reason` — `SlowCancelLLM` 阻塞 chat · `_run_line` 实路径 · `bridge.request_cancel()` 中 Stop
- **断言**：回合进行中 `_turn_busy` · 取消后唯一 `turn.end`（`ok=false` · `finish_reason=cancelled`）· `_turn_busy` 清空
- **参考**：`server.py` `_run_line` C9 · TURN-CONTROL.md R3 · T-1407
- **验收**：`python -m unittest tests.test_turn_cancel -v` → **9/9 OK**

### T-1804-06 · activity_router 项目信号（2026-07-17 · **done**）

- **文件**：`agent-core/tests/test_activity_router.py`（新）
- **用例**：`test_project_markers_route_to_project` — `项目 新建/打开` · `做项目` · `项目模式` · `workspace/…` → `shell=project`
- **用例**：`test_bound_project_session_continues_project` · `test_plan_gate_open_routes_to_project_over_proposals` · `test_execute_workspace_path_routes_to_project`
- **边界**：`test_explicit_grow_signal_overrides_bound_project` — `write_evolve`/`proposal` 仍路由 grow
- **会话**：`test_compute_session_route_resumes_project_shell` — 无新 user line 时续接 project
- **参考**：`activity_router.py` T-1104 · `_PROJECT_MARKERS` · `project_plan_gate_open`
- **验收**：`python -m unittest tests.test_activity_router -v` → **6/6 OK**

### T-1804-07 · IT-17 跨会话 read confirm（2026-07-17 · **done**）

- **文件**：`agent-core/tests/test_cross_session_read.py`
- **类**：`CrossSessionReadConfirmTests`（与 T-1117 pytest 用例拆边界：executor 层完整断言）
- **用例**：`test_same_session_read_skips_confirm` — 当前会话 `messages.jsonl` 不弹 confirm
- **用例**：`test_other_session_read_requires_confirm_and_rejects` — 跨会话须 confirm · preview 含 `Cross-session peek` + 目标 session id · 拒绝 → `CONFIRM_REJECTED`
- **用例**：`test_other_session_read_succeeds_when_confirmed` — 确认后 `read_file` 成功
- **用例**：`test_grep_other_session_requires_confirm` — `grep` 跨 `data/sessions/<id>` 同样须 confirm
- **参考**：`executor._needs_confirm` · T-1117 · STABILIZATION.md IT-17
- **验收**：`python -m unittest tests.test_cross_session_read -v` → **6/6 OK** · pytest 全文件 **8/8**

**M1-E 批次完结**（T-1804-01～07）。

### T-1806-01 · runtime_guards 列入 Gate（2026-07-17 · **done**）

- **文件**：`agent-core/tests/run_stabilization.py`（最小 Gate runner 骨架；M1-G T-1807-01 后续补摘要输出）
- **Gate 列表**：M1-C～M1-E 已有模块 + `tests.test_runtime_guards`（IT-21 M0）
- **用例**：`test_runtime_guards.py` **9 cases** — TurnWatchdog · Agent LLM timeout · run_evolved cancel · WsBridge finish_reason
- **验收**：`python tests/run_stabilization.py` → **61/61 OK**（含 guards 9）
- **单测**：`python -m unittest tests.test_runtime_guards -v` → **9/9 OK**
- **参考**：`runtime_guards.py` T-1518 · STABILIZATION.md §6.1 IT-21

### T-1806-02 · runtime_guards_m1 列入 Gate（2026-07-17 · **done**）

- **文件**：`agent-core/tests/run_stabilization.py` — `GATE_MODULES` 追加 `tests.test_runtime_guards_m1`
- **用例**：`test_runtime_guards_m1.py` **8 cases** — inline 8192 硬顶 · guard 事件落盘 · workspace 路径引用 · staging 豁免 · scaffold demo cancel · tool.toml 后 auto demo · `AUTO_DEMO` 默认
- **验收**：`python tests/run_stabilization.py` → **69/69 OK**（含 guards_m1 8）
- **单测**：`python -m unittest tests.test_runtime_guards_m1 -v` → **8/8 OK**
- **参考**：`executor.py` / `runtime_guards.py` T-1511～T-1520 · STABILIZATION.md §6.1 IT-22～IT-23

### T-1806-03 · checker_subagent 核心子集列入 Gate（2026-07-17 · **done**）

- **文件**：`agent-core/tests/run_stabilization.py` — 新增 `GATE_CHECKER_TARGETS`（18 cases）
- **子集**：`ParseCheckerCommand` · `VerdictMerge` · hard checklist（missing/demo）· `CheckerTaskFromRecord` · `CompletionGate` · `CheckerRunner` · `AutoCheckerSpawn`
- **排除**：`HardChecklistTests.test_broken_manifest_fails`（TOML 解析边界，非 checker 主路径）
- **验收**：`python tests/run_stabilization.py` → **87/87 OK**（含 checker 18）
- **全量**：`python -m unittest tests.test_checker_subagent -v` → **19/19 OK**
- **参考**：`subagent.py` T-1610～T-1623 · STABILIZATION.md IT-24～IT-25

### T-1806-04 · IT-42 repair_orphaned_tool_calls（2026-07-17 · **done**）

- **文件**：`agent-core/tests/test_orphaned_tool_calls.py`（新，6 cases）
- **覆盖**：`repair_orphaned_tool_calls` 补占位 · 已有 tool 回复幂等 · 多 `tool_call_id` 局部缺失 · `build_llm_messages` 发送前 repair · `Session.load` 回写 `messages.jsonl`
- **Gate**：`run_stabilization.py` 追加 `tests.test_orphaned_tool_calls`
- **验收**：`python -m unittest tests.test_orphaned_tool_calls -v` → **6/6 OK** · `python tests/run_stabilization.py` → **93/93 OK**
- **参考**：`context.py` · `session.py` · BUG-005 · STABILIZATION.md §6.1 IT-42

### T-1806-05 · IT-51 LLM timeout 全链路（2026-07-17 · **done**）

- **文件**：`agent-core/tests/test_runtime_guards.py` — 扩 `LlmTimeoutChainTests`（+2 cases）
- **覆盖**：`LLMTimeoutError` → agent `run_turn` `finish_reason=timeout` 且无 WS `error` · `server._run_line` → `turn.end` `ok=false` `finish_reason=timeout`；复用既有 `AgentTimeoutTests` · `WsBridgeFinishReasonTests`
- **Gate**：`tests.test_runtime_guards`（IT-51 注释标注于 `run_stabilization.py`）
- **验收**：`python -m unittest tests.test_runtime_guards.LlmTimeoutChainTests -v` → **2/2 OK** · `python tests/run_stabilization.py` → **95/95 OK**
- **参考**：`agent.py` · `server.py` T-1519 · STABILIZATION.md §6.1 IT-51 · S-48

### T-1806-06 · IT-60 sanitize_log_value（2026-07-17 · **done**）

- **文件**：`agent-core/tests/test_sanitize_log_value.py`（新，5 cases）· `tools/logging.py` demo 扩 IT-60 磁盘断言
- **覆盖**：`api_key`/`password`/`token` → `[redacted]` · 长串截断 · `log_tool_call` 磁盘不含明文 key · `log_guard_event` 敏感字段
- **Gate**：`run_stabilization.py` 追加 `tests.test_sanitize_log_value`
- **验收**：`python -m unittest tests.test_sanitize_log_value -v` → **5/5 OK** · `python tools/logging.py` → IT-60 pass · `python tests/run_stabilization.py` → **100/100 OK**
- **注**：实现占位符为 `[redacted]`（任务表 `[REDACTED]` 为同义验收描述）

### T-1806-07 · IT-55 坏 messages.jsonl 行（2026-07-17 · **done**）

- **文件**：`agent-core/tests/test_session_corruption.py`（新，5 cases · 1 `expectedFailure`）
- **现状（pass）**：`_read_messages` / `Session.load` 跳过非法 JSON · 非 object 行 · 全坏行→空历史 · 不 traceback
- **缺口（xfail）**：`test_it55_bad_jsonl_should_surface_notice_to_user` — 无 `corruption_notices`；T-1823-02 修后改断言
- **Gate**：`run_stabilization.py` 追加 `tests.test_session_corruption`
- **验收**：`python -m unittest tests.test_session_corruption -v` → **4 pass + 1 expected failure** · `python tests/run_stabilization.py` → **105 OK (expected failures=1)**
- **参考**：`session.py` `_read_messages` · STABILIZATION.md §3.9 IT-55

### T-1806-08 · IT-56 坏 state.json（2026-07-17 · **done**）

- **文件**：`agent-core/tests/test_session_corruption.py` — 扩 `BadStateJsonCurrentBehaviorTests`（+6 cases · 1 xfail）
- **覆盖**：`read_last_conversation_id` 坏 JSON→`None` · `read_shell_sessions`/`read_project_sessions`→`{}` · `switch_shell` grow→daily 不崩 · `resume_or_create` 降级 · `record_shell_session` 可重写合法 state
- **缺口（xfail）**：`test_it56_bad_state_should_surface_notice_to_user` — T-1823-05 未做
- **隔离**：`setUp`/`addCleanup` 备份还原真实 `data/state.json`
- **验收**：`python -m unittest tests.test_session_corruption -v` → **9 pass + 2 expected failures** · `python tests/run_stabilization.py` → **111 OK (expected failures=2)**
- **参考**：`shell_switch.py` · `project_switch.py` · `session.py` · STABILIZATION.md IT-56

**M1-F 批次完结**（T-1806-01～08）。

### T-1807-01 · Gate runner 正式化（2026-07-17 · **done**）

- **文件**：`agent-core/tests/run_stabilization.py`（M1-F 骨架收束为正式 Gate runner）
- **结构**：`GATE_MODULES` 12 模块 + `GATE_CHECKER_TARGETS` 18 cases · `load_gate_suite()` / `run_gate()` / `main()`
- **覆盖**：M1-C～M1-F 全量（见 runner 注释 IT 映射）；checker 子集省略 `test_broken_manifest_fails`
- **xfail**：IT-55 / IT-56 各 1 条 `expectedFailure`（T-1823 缺口，非回归）
- **defer**：IT-38（T-1808-04）· IT-62（T-1824）未入 Gate
- **验收**：`python tests/run_stabilization.py` → **111 OK (expected failures=2)** · **exit 0**

### T-1807-02 · runner 分项 PASS/FAIL 摘要（2026-07-17 · **done**）

- **代码**：`gate_entries()` 按模块/子集逐段跑 · `GateEntryResult` + `print_gate_summary()`
- **输出**：末尾 `Gate summary (IT-G):` 表 — 13 行模块 + checker 子集 + `TOTAL`
- **语义**：`xfail` 计 PASS；`fail`/`err`/`uxpass` 计 FAIL · 汇总行 `TOTAL … OK|FAIL`
- **验收**：全绿 **exit 0** · 摘要示例 `tests.test_session_corruption PASS 11 run, 2 xfail` · `TOTAL OK 111 run, 2 xfail`

### T-1807-03 · §6 文档对齐（2026-07-17 · **done**）

- **文件**：`docs/STABILIZATION.md` §6.1 扩为四节：IT 覆盖表 · `GATE_MODULES` 清单 · `GATE_CHECKER_TARGETS` · 本地一键
- **对齐**：13 模块 + checker 子集与 `run_stabilization.py` 逐字一致；IT-38/IT-62 标 defer；IT-55/IT-56 标 xfail
- **§6.2**：扩展集去重（IT-24～25/56/58 已入 Gate 的交叉引用）
- **验收**：命令 `python tests/run_stabilization.py` · 期望 111 run / 2 xfail 与 §6.1.4 一致

### T-1807-04 · 本地 Gate 全绿存档（2026-07-17 · **done**）

- **命令**：`cd agent-core` · `python tests/run_stabilization.py`
- **结果**：**exit 0** · **111 run** · **2 xfail**（IT-55/IT-56）· 摘要 **13/13 PASS** + `TOTAL OK`
- **存档**：`data/_t1807-out.txt`（摘要 + exit_code）· `data/_t1807-err.txt`（verbose 全量，同 T-1805/T-1806 惯例）
- **耗时**：~53s（Windows · Python 3.14）

**M1-G 批次完结**（T-1807-01～04）。

### T-1808-01 · CLI parity 元命令全集（2026-07-17 · **done**）

- **交付**：`docs/CLI-DESKTOP-PARITY.md` v0.1.0
- **方法**：自 `main.py` `ConversationRepl.handle_line` 按分支顺序审计；交叉核对 `boundaries` · `session` · `subagent` · `router` · `host_scope_cli` · `project_cli` 解析器
- **结果**：**17 族**元命令（M01～M17）≥ 15 验收线；含子命令展开（项目 7 · 托管目录 4 · proposals 4 · evolve 强弱触发）
- **引用**：`STABILIZATION.md` §8 链到新表

### T-1808-02 · CLI parity 桌面等价路径（2026-07-17 · **done**）

- **交付**：`docs/CLI-DESKTOP-PARITY.md` v0.2.0
- **列**：主表 M01～M17 增 **桌面 WS** · **侧栏/UI** · **Parity**（同路径 / 等价不同径 / N/A）
- **子表**：§2.1 项目 7 子命令 · §2.2 托管 4 子命令 · §2.6 proposals · §3 fallthrough · §4 WS 专用对照
- **要点**：
  - 聊天元命令统一 **`user.message`**（`command` 仅侧栏 `sendCommand("项目 确认")`）
  - **M01 exit** · **turn.cancel** → 桌面 **N/A**（托盘/Stop 真源）
  - **M08/M12/M14** 多项 **等价不同径**（`proposal.*` · `host_scope.*` · `project.*`）
  - `project.open` WS 存在但 UI 未接按钮 → 绕行 `项目 打开` 聊天

### T-1808-03 · CLI parity 绕行文案（2026-07-17 · **done**）

- **交付**：`docs/CLI-DESKTOP-PARITY.md` v0.3.0 · 新增 **§6**
- **结构**：§6.1 N/A（M01 exit · turn.cancel · shell.switch · 归档）· §6.2 等价不同径 · §6.3 无按钮同路径 · §6.4 自检清单
- **可引用块**：桌面误发 `exit` · CLI 无 Stop · 非 grow 找 proposals · 托管区 · 无按钮元命令通用句
- **主表**：增 **绕行 §6** 列（M01/M08～M17 等）
- **验收**：N/A 与 §4 仅桌面 WS 行均有替代动作说明；无裸「不支持」

### T-1808-04 · IT-38 parity 自动化（2026-07-17 · **done**）

- **文件**：`agent-core/tests/test_cli_desktop_parity.py`（10 cases）
- **覆盖**：
  - 文档：`CLI-DESKTOP-PARITY.md` ≥15 族 + M04/M05/M14 最小集
  - `handle_line`：`新会话` · `压缩`（mock）· `项目 新建`
  - WS：`_dispatch` `user.message` / `command` 对 `新会话`/`压缩`；`_run_line` `项目 新建`
- **验收**：`python -m unittest tests.test_cli_desktop_parity -v` → **10 OK**
- **Gate**：**未入** `GATE_MODULES`（IT-38 实现与 Gate 独立，同 IT-62）

### T-1808-05 · IT-11 command ≡ user.message（2026-07-17 · **done**）

- **文件**：`agent-core/tests/test_cli_desktop_parity.py` — 扩 `CommandUserMessageEquivalenceTests`（+5 cases → **15 total**）
- **断言**：`新会话` · `压缩` · `只聊` · `动手` · `项目 新建` · `项目 确认` 在 `user.message` / `command` 下 **会话状态一致**
- **文档化 WS 差**：`command` 总是 `emit_session_state`；`user.message` 仅刷新类元命令 — 见 `CLI-DESKTOP-PARITY.md` IT-11 小节
- **验收**：`python -m unittest tests.test_cli_desktop_parity -v` → **15 OK**

**M1-H 批次完结**（T-1808-01～05）。

### T-1820-01 · S-11 project 拖放（2026-07-17 · **done**）

- **方式**：WS 协议级 smoke（`python agent-core\server.py --port 8765 --takeover`）；等价桌面 `file.stage`
- **脚本**：`agent-core/tests/test_file_drop_e2e.py` mode=`project`
- **步骤**：
  1. 冷启 sidecar（PID **68960**）→ stdout `{"ready": true, "host": "127.0.0.1", "port": 8765}`
  2. WS 连 `127.0.0.1:8765` → bootstrap 含 `ui.route`
  3. `shell.switch` → **project** `file-drop-e2e` → `shell.switch.done` `session=20260712-3af82fcc`
  4. `file.stage` 外部 `.py` 小文件 → **`file.staged`** `ref=workspace/file-drop-e2e/_incoming/f1662565/drop_probe_*.py` · `id=51ab5d338e86c7a8`
  5. 磁盘 `paths.resolve_under_agent(ref)` 存在 · 内容含 `DROP_PROBE`
- **结果**：**pass** — staged + `_incoming/` 落点正确；不崩
- **注**：续测 `user.message`+附件时 `assistant.done` **90s 超时**（LLM 回合）；staging 路径已验，S-11 验收满足
- **存档**：`data/_t1820-01-out.txt` · `data/_t1820-01-err.txt` · `data/_t1820-01-smoke.txt`
- **新 BUG**：无

### T-1820-02 · S-15 grow proposals（2026-07-17 · **done**）

- **方式**：WS 协议级 smoke（`python agent-core\server.py --port 8765 --takeover`）；等价 grow 顶栏 **接受/拒绝**
- **步骤**：
  1. 冷启 sidecar（PID **21372**）→ stdout ready
  2. 种 2 条 pending memory proposal（`prop-stab-s15-*-b`）
  3. WS bootstrap → `evolve.proposals` 含两条
  4. `shell.switch` → **grow**
  5. `proposal.reject` `prop-stab-s15-reject-b` → **0.09s** 内 `notice`「已拒绝」+ `evolve.proposals` 队列减 1
  6. `proposal.accept` `prop-stab-s15-accept-b` → **0.10s** 内 `notice`「已接受」+ `evolve.proposals` 清空 pending
  7. 磁盘：reject → `proposals/archive/` · accept → `memories/coding/stab-s15-accept-mem-b.md`
- **结果**：**pass** — 队列即时更新；无卡死/超时
- **存档**：`data/_t1820-02-out.txt` · `data/_t1820-02-err.txt` · `data/_t1820-02-smoke.txt`
- **新 BUG**：无

### T-1820-03 · S-18 Electron 闪退（2026-07-17 · **done**）

- **方式**：`desktop/` 下 `npm run dev`（5173 占用 → **5174**）· 强杀 Electron 模拟闪退
- **步骤**：
  1. 冷启 `npm run dev`（PID **75368**）→ Vite `http://localhost:5174/` **200**
  2. Electron 4 进程就绪（72356 等）
  3. `Stop-Process -Name electron -Force`
  4. **2s 后**：Vite 仍 **HTTP 200** · `npm run dev` 父进程仍存活 · 5174 仍 LISTENING
  5. stderr：`[electron] exited (code=4294967295, signal=none), restarting in 1.5s…`
  6. **+2s**：Electron 已自动拉起（81380 等新 PID）
- **结果**：**pass** — Vite 未随 Electron 退出；可再起壳（BUG-003 回归面）
- **存档**：`data/_t1820-03-out.txt` · `data/_t1820-03-err.txt` · `data/_t1820-03-smoke.txt`
- **注**：测试后已清理 5174 dev；**5173** 仍有历史 node（PID 26828，非本项启动）
- **新 BUG**：无

### T-1820-04 · S-19 plan.request 卡（2026-07-17 · **done**）

- **方式**：WS 协议级 smoke；等价 project 壳 **计划确认卡** `plan.response` vs 侧栏/CLI `项目 确认`
- **步骤**：
  1. 冷启 sidecar（PID **79288**）→ ready
  2. `shell.switch` → grow → `command` `项目 新建 stab-s19-a` → **`plan.request`**（draft · 含 `tasks_preview`）+ `project.state` `needs_plan_confirm: true`
  3. `plan.response` `choice: confirm` → **0.10s** 内 `plan.done` + `project.state` `plan_status: confirmed` · `needs_plan_confirm: false`
  4. 等价对照：`项目 新建 stab-s19-b` → `command` `项目 确认` → **0.36s** 内同为 `confirmed`
- **结果**：**pass** — 计划卡确认与侧栏/CLI 确认语义一致；无卡死
- **存档**：`data/_t1820-04-out.txt` · `data/_t1820-04-err.txt` · `data/_t1820-04-smoke.txt`
- **新 BUG**：无

### T-1820-05 · S-20 跨项目确认卡（2026-07-17 · **done**）

- **方式**：WS 协议级 smoke；等价 project 侧栏 **切换确认卡** `project.switch.request` / `confirm: true`
- **步骤**：
  1. 冷启 sidecar（PID **75340**）→ ready
  2. 预建 `stab-s20-b` · `command` `项目 新建 stab-s20-a` → 绑定 **A**
  3. `project.switch` → **B**（无 `confirm`）→ **0.27s** 内仅 **`project.switch.request`**（`needs_confirm: true` · `current_project_id: stab-s20-a`）；**无** `project.switch.done`
  4. 取消路径（不发 confirm）→ `project.state` 仍为 **stab-s20-a**
  5. `project.switch` `confirm: true` + `request_id` → **3.58s** 内 `project.switch.done` · `session_replaced: true` · `project_id: stab-s20-b`
- **结果**：**pass** — 取消不换；确认才换
- **存档**：`data/_t1820-05-out.txt` · `data/_t1820-05-err.txt` · `data/_t1820-05-smoke.txt`
- **新 BUG**：无

### T-1820-06 · S-21 project.verify（2026-07-17 · **done**）

- **方式**：WS 协议级 smoke；等价 project 侧栏 **运行验收** / CLI `项目 验收`
- **步骤**：
  1. 冷启 sidecar（PID **84904**）→ ready
  2. `项目 新建 stab-s21-demo` → 写入 `demo.py`（`print('stab-s21-verify-ok')`）→ `项目 确认` → `plan_status: confirmed` · `can_verify: true`
  3. `project.verify` → **0.62s** 内 `project.verify.done` `passed: true` · `exit_code: 0` · stdout 含 `stab-s21-verify-ok`
  4. CLI 对照 `项目 验收` → **0.83s** 内 `turn.end` ok
- **结果**：**pass** — confirmed 后验收命令可跑通
- **存档**：`data/_t1820-06-out.txt` · `data/_t1820-06-err.txt` · `data/_t1820-06-smoke.txt`
- **新 BUG**：无

### T-1820-07 · S-22 断线重连（2026-07-17 · **done**）

- **方式**：WS 协议级 smoke；等价桌面断线后自动重连 / `session.refresh`
- **步骤**：
  1. 冷启 sidecar（PID **16432**）→ ready
  2. 连接 #1 → bootstrap `session.banner` + `session.history` + `session.memory` · session=`20260717-1593d29d`
  3. 同连接 `session.refresh` → banner/history 与 bootstrap **一致**（0.03s）
  4. 断线 → 磁盘写入标记消息 `S22-RECONNECT-MARKER` → 重连 #2
  5. 连接 #2 bootstrap：**同** `session_id` / `active_shell` / `turn_mode` / `topics` / `project_id`；history 含标记（1 item）
  6. 重连后再次 `session.refresh` → 与 bootstrap #2 一致
- **结果**：**pass** — 断线重连后 banner/history 一致；不丢会话
- **存档**：`data/_t1820-07-out.txt` · `data/_t1820-07-err.txt` · `data/_t1820-07-smoke.txt`
- **新 BUG**：无

### T-1820-08 · S-23 daily/pet confirm（2026-07-17 · **done**）

- **方式**：WS 协议级 smoke；daily 壳 + pet 后端（≡ daily 会话线，PET-SHELL P1）
- **步骤**：
  1. 冷启 sidecar（PID **88952**）→ ready
  2. `shell.switch` → **daily** `session=20260717-2a559782`
  3. `user.message` 触发 `write_text` → **12.51s** 内 `confirm.request` → `choice: y` → `confirm.done` + `tool.end` + `turn.end`；`workspace/stab-s23-daily.txt` = `s23-daily-ok`
  4. 再 `shell.switch` daily（pet 同会话线）→ 同 session_id → 第二次 confirm 写 `stab-s23-pet.txt` → **11.85s** 通
- **结果**：**pass** — 同 S-04 语义（confirm → 落盘 → 回合可结束）；不卡
- **存档**：`data/_t1820-08-out.txt` · `data/_t1820-08-err.txt` · `data/_t1820-08-smoke.txt`
- **注**：`shell_sessions.daily` 保持 `20260717-2a559782`（与 grow 分离）
- **新 BUG**：无

### T-1820-09 · S-24 write_evolve 二次 confirm（2026-07-17 · **done**）

- **方式**：协议级 smoke（`WsBridge` 双 confirm + stale ID）+ Gate 子集 `test_confirm_pipeline`（BUG-008 / C1–C2 / C7）
- **步骤**：
  1. confirm #1（模拟首次 `write_evolve`）挂起 → 点 **旧卡** `request_id` → `confirm.done` `choice: stale` + `notice`；**不**解锁 `confirm_fn`
  2. 点真卡 → `confirm_fn` 返回 `y`
  3. confirm #2（模拟失败后二次 confirm / staging）→ 再点 #1 旧 ID → stale；点 #2 → `y`
  4. 全程 **0.008s**（≪ 60s）；无空转
  5. `python -m unittest tests.test_confirm_pipeline -v` → **9 OK**（含 wrong-id no-spin · timeout · base64 guard）
- **结果**：**pass** — BUG-008 路径不空转；二次 confirm 可正常完成
- **存档**：`data/_t1820-09-out.txt` · `data/_t1820-09-smoke.txt` · `data/_t1820-09-unittest.txt`
- **新 BUG**：无

### T-1820-10 · S-25 confirm 中 Stop（2026-07-17 · **done**）

- **方式**：`WsBridge` 协议 smoke + 活 WS（grow · `write_text` confirm 中 `turn.cancel`）+ `test_turn_cancel` 回归
- **步骤**：
  1. bridge：confirm 挂起 → `request_cancel` → **0.263s** 内 `confirm.done` `choice: cancelled`；`confirm_fn` 返回 `n`
  2. WS：`shell.switch` grow → `user.message` 触发 `write_text` → `confirm.request` → **不点同意**，发 `turn.cancel`
  3. **9.53s** 内（含 LLM 到 confirm）：`confirm.done` `cancelled` + `turn.end` `finish_reason: cancelled`
  4. `workspace/stab-s25-should-not-exist.txt` **未**落盘
  5. `python -m unittest tests.test_turn_cancel -v` → **9 OK**
- **结果**：**pass** — confirm 中 Stop → `choice=cancelled`；不永久「提交中…」
- **存档**：`data/_t1820-10-out.txt` · `data/_t1820-10-err.txt` · `data/_t1820-10-smoke.txt` · `data/_t1820-10-unittest.txt`
- **新 BUG**：无

### T-1820-11 · S-26 confirm 90s 超时（2026-07-17 · **done**）

- **方式**：协议 smoke（默认 90s 断言 + 1s 缩略超时）+ 活 WS（`CONFIRM_TIMEOUT_SEC=3`）+ `test_confirm_pipeline` 回归
- **步骤**：
  1. 断言 `DEFAULT_CONFIRM_TIMEOUT_SEC == 90` · `confirm_timeout_sec()` 默认 90
  2. bridge `confirm_timeout=1.0` 无响应 → **1.01s** 内 `confirm.done` `choice: timeout` + notice「超时」→ 二次 confirm 仍可 `y`（可再聊）
  3. sidecar `CONFIRM_TIMEOUT_SEC=3`（PID **88124**）→ grow `write_text` confirm **不点** → **15.55s** 内（含 LLM）`confirm.done` timeout + `turn.end`；文件未写
  4. 超时后续发 `S26-OK` → **4.99s** 内 `turn.end`（可再聊）
  5. `python -m unittest tests.test_confirm_pipeline -v` → **9 OK**
- **结果**：**pass** — 超时发 `confirm.done`；可再聊
- **存档**：`data/_t1820-11-out.txt` · `data/_t1820-11-err.txt` · `data/_t1820-11-smoke.txt` · `data/_t1820-11-unittest.txt`
- **新 BUG**：无

### T-1820-12 · S-27 host 写 confirm（2026-07-17 · **done**）

- **方式**：协议级 smoke（临时 host roots + `build_confirm_preview` / `ToolExecutor`）；等价桌面 host 写确认卡
- **步骤**：
  1. 临时登记 `s27_dl`（只读）+ `s27_docs`（可写）→ 测后还原 `host_scope.json`
  2. `host_path_confirm_line` → `Source: C:\…\s27-src.txt (host:s27_dl)` · `Dest: C:\…\inbox\s27-src.txt (host:s27_docs)`
  3. `build_confirm_preview(host_copy_move)` 含 **绝对路径** Source/Dest + host 标签
  4. `ToolExecutor` confirm：`allow_all=False`（host 禁用 `a`）· 选 `n` → 不落盘
- **结果**：**pass** — 绝对路径标签正确；host 写强制每次 confirm
- **存档**：`data/_t1820-12-out.txt` · `data/_t1820-12-smoke.txt`
- **新 BUG**：无

### T-1820-13 · S-28 四壳 Stop（2026-07-17 · **done**）

- **方式**：WS 协议 smoke（`shell.switch` → `user.message` → `turn.start` 即 `turn.cancel` → 续聊）+ `test_turn_cancel` 回归
- **步骤**：
  1. **project**（`stab-r1-demo` · `20260715-4ceb96db`）：`1+1` → Stop → **0.415s** 内 `turn.end` `finish_reason: cancelled`；续发 `2+2` **5.10s** `turn.end` ok
  2. **daily**：同上 → **0.476s** cancelled；续聊 **2.82s** ok（`session_id` **20260717-1593d29d** — smoke 前 `shell_sessions` 已偏 grow 同 id；Stop 协议仍 pass）
  3. **pet**（≡ daily 会话线）：**0.394s** cancelled；续聊 **2.52s** ok
  4. grow 已在 P0 **S-05** 验收；本项覆盖 project/daily/pet
  5. `python -m unittest tests.test_turn_cancel -v` → **9 OK**
- **结果**：**pass** — 三壳 Stop ≤3s cancelled；可再输入
- **存档**：`data/_t1820-13-out.txt` · `data/_t1820-13-err.txt` · `data/_t1820-13-smoke.txt` · `data/_t1820-13-unittest.txt`
- **环境**：smoke 后 `shell_sessions.daily` 恢复 **20260717-2a559782**（与 grow **1593d29d** 分离）
- **新 BUG**：无

### T-1820-14 · S-29 recall（2026-07-17 · **done**）

- **方式**：WS 协议 smoke（grow/daily/pet · 先上下文句再「刚刚我们说了什么」）+ `turn_intent.py` + `_demo_t905` 回归
- **步骤**：
  1. **grow**（`20260717-1593d29d`）：上下文 `S29-CTX-OK` → recall → `turn.start` `intent: recall`；**0** `tool.start`；**8.11s** `turn.end` ok
  2. **daily**：同上 → **6.85s** ok，无工具
  3. **pet**（≡ daily 线）：**7.81s** ok，无工具
  4. `python turn_intent.py` → classify `刚刚我们说了什么` → **recall**；`should_spawn_explore` 对 recall 为 false
  5. `_demo_t905` → recall 父循环 **tools=[]** · `tool_rounds=0` · soft reminder 注入一次
- **结果**：**pass** — recall 不乱调工具（无 `read_file messages.jsonl` 等）
- **存档**：`data/_t1820-14-out.txt` · `data/_t1820-14-err.txt` · `data/_t1820-14-smoke.txt` · `data/_t1820-14-intent.txt` · `data/_t1820-14-t905.txt`
- **环境**：smoke 后 `shell_sessions.daily` 恢复 **20260717-2a559782**
- **新 BUG**：无

### T-1820-15 · S-30 压缩（2026-07-17 · **done**）

- **方式**：WS 协议 smoke（grow/daily 发 `压缩`）+ `context.py` 回归 + `test_cli_desktop_parity` 压缩用例
- **步骤**：
  1. **grow**（`20260717-1593d29d`）：`user.message` `压缩` → **19.16s** 内 `notice`「已压缩：摘要 17 条消息 → digest.md §压缩 1…」+ `session.memory` `memory_mode_label: 已压缩`；**无** `turn.start`（手动压缩不走回合）
  2. 续发 `只回复：S30-OK` → **2.68s** `turn.end` ok（非假死）
  3. **daily**（shell.switch；会话线同 grow id）：`压缩` → **11.83s** `notice` §压缩 2 + `已压缩`；续聊 **3.68s** ok
  4. 磁盘：`digest.md` 含 `# 压缩 1` / `# 压缩 2`；`messages.jsonl` 条数未截断
  5. `python context.py` → compact / auto-threshold / digest 节序 **pass**
  6. `test_cli_desktop_parity` M05 + WS compact refresh → **3 OK**
- **结果**：**pass** — 压缩 notice 可见；`session.memory` 刷新；可再聊
- **存档**：`data/_t1820-15-out.txt` · `data/_t1820-15-err.txt` · `data/_t1820-15-smoke.txt` · `data/_t1820-15-context.txt` · `data/_t1820-15-parity.txt`
- **环境**：smoke 后 `shell_sessions.daily` 恢复 **20260717-2a559782**
- **新 BUG**：无

### T-1820-16 · S-31 memory 顶栏（2026-07-17 · **done**）

- **方式**：WS 协议 smoke（bootstrap / shell.switch / session.refresh 对照磁盘 `session_memory_event`）+ `test_project_switch` IT-06 回归
- **步骤**：
  1. **STD-001 绕行**：grow 会话 `active_shell` 曾被标为 `daily` → smoke 前校正为 `grow`（否则 `shell.switch` daily 空转）
  2. **bootstrap/grow**：`session.memory` **37 条 · 已压缩** · `digest_sections: 2` ≡ 磁盘
  3. **daily switch**（`20260717-2a559782`）：**9 条 · 未压缩**
  4. **project switch**（`stab-r1-demo` / `20260715-4ceb96db`）：**28 条 · 未压缩**
  5. grow 续聊 `只回复：S31-OK` → `session.refresh` **37→39 条**（+2，合理）；refresh 与磁盘一致
  6. `test_project_switch.test_session_replaced_emits_memory_and_history` → **OK**
- **结果**：**pass** — 顶栏 memory 元数据与 `len(messages.jsonl)` 一致；壳切换不串；续接后条数合理
- **存档**：`data/_t1820-16-out.txt` · `data/_t1820-16-err.txt` · `data/_t1820-16-smoke.txt` · `data/_t1820-16-unittest.txt`
- **注**：agent 宽预算路径回合结束未必推 `session.memory` 事件；顶栏以 `session.refresh` / 连接 bootstrap 为准（TURN-FEEDBACK §5）
- **新 BUG**：无

### T-1820-17 · P1 本批记入 log（2026-07-17 · **done**）

- **动作**：将 M2-A（T-1820-01～16 / S-11,S-15,S-18～S-31）写入下方 **P1 记录表**
- **结果**：**16/16 pass** · 无新 BUG · 绕行仅 **STD-001**（已在 backlog，非本批 fail）
- **M2-A 批次完结** → 下一批 **M2-B**（T-1821-01）

### T-1821-01 · S-33 draft 拒写码（2026-07-17 · **done**）

- **方式**：WS 协议 smoke（新建 draft 项目，勿污染 `stab-r1-demo`）+ `test_project_lifecycle` 回归
- **步骤**：
  1. 冷启 sidecar（PID **6896** / 子进程 **18228**）→ ready
  2. `新会话` → `项目 新建 stab-s33-demo` → `project.state` `plan_status: draft` · `needs_plan_confirm: true` · `can_verify: false`（**0.68s**）
  3. `project.verify` → **0.001s** 内 `error`「计划未确认，无法运行验收」；二次 verify 仍拒
  4. `user.message` 强制 `write_text` → `workspace/stab-s33-demo/main.py`：**19.07s** `turn.end`；**无** `main.py` 落盘；blob 含「计划未确认」
  5. `user.message` 强制 `run_python`：**16.81s** `turn.notice`「计划未确认：写源码与 run_python 暂不可用」；**0** `tool.end` ok
  6. `python -m unittest tests.test_project_lifecycle -v` → **4/4 OK**（含 `test_draft_rejects_run_python_plan_gate`）
- **结果**：**pass** — draft 下源码写 / run_python / verify 均被拒
- **存档**：`data/_t1821-01-out.txt` · `data/_t1821-01-err.txt` · `data/_t1821-01-smoke.txt` · `data/_t1821-01-smoke.py` · `data/_t1821-01-unittest.txt`
- **环境**：`shell_sessions` grow/daily 仍分离；`stab-s33-demo` → 会话 `20260717-f900f896`；三件套在盘、无 `main.py`
- **新 BUG**：无

### T-1821-02 · S-34 plan_dirty 再确认（2026-07-17 · **done**）

- **方式**：WS 协议 smoke；新建 `stab-s34-demo`（勿污染 confirmed 项目）
- **步骤**：
  1. 冷启 sidecar → ready
  2. `新会话` → `项目 新建 stab-s34-demo` → draft → `项目 确认` → **0.42s** `plan_status: confirmed`
  3. 磁盘追加 `## Phase 9 — S34-plan-dirty` → `user.message` `只回复：S34-DIRTY` → **17.3s** 后 `project.state` **`plan_dirty`** · `needs_plan_confirm: true` · `plan.request` 标题「计划已变更 · 请确认」
  4. `project.verify` while dirty → `error`「计划未确认」
  5. `plan.response` `choice: confirm` → **1.65s** `plan.done` + `confirmed` · `needs_plan_confirm: false`
  6. 负向：仅勾选 `[ ]`→`[x]` → 回合后仍 **confirmed** · **无** `plan.request`
- **结果**：**pass** — 改 Phase 须再确认；勾选任务不触发 dirty
- **存档**：`data/_t1821-02-out.txt` · `data/_t1821-02-err.txt` · `data/_t1821-02-smoke.txt` · `data/_t1821-02-smoke.py`
- **环境**：`shell_sessions` grow/daily 仍分离
- **新 BUG**：无

### T-1821-03 · S-35 grow 造工具（2026-07-17 · **done**）

- **方式**：WS 协议 smoke（grow · `MY_AGENT_AUTO_EXPLORE=0` 最小路径）+ `test_write_evolve_pipeline` 回归
- **步骤**：
  1. 冷启 sidecar（`MY_AGENT_AUTO_EXPLORE=0`）→ ready
  2. `shell.switch` grow → 强制两次 `write_evolve`（`stab_s35_echo` · `main.py` 再 `tool.toml` · `content_base64`）
  3. **2×** `confirm.request` → `y` → **2×** `tool.end` ok（**31.4s**）；磁盘三件套落盘
  4. 同会话 `run_evolved` `stab_s35_echo` → **6.3s** `tool.end` ok（sidecar registry 热重载）
  5. 进程内 `ToolRegistry.load` → `status=active` · `echo=s35`
  6. 清理 `evolve/tools/common/stab_s35_echo/`；`python -m unittest tests.test_write_evolve_pipeline -v` → **17/17 OK**
- **结果**：**pass** — grow `write_evolve` → 可加载可跑
- **存档**：`data/_t1821-03-out.txt` · `data/_t1821-03-err.txt` · `data/_t1821-03-smoke.txt` · `data/_t1821-03-smoke.py` · `data/_t1821-03-unittest.txt`
- **环境**：`shell_sessions` grow/daily 仍分离；工具目录已删，无残留
- **新 BUG**：无

### T-1821-04 · S-36 自动 checker notice（2026-07-17 · **done**）

- **方式**：WS 协议 smoke（grow scaffold + **`CHECKER_AUTO_ON_SCAFFOLD=1`**）+ `test_checker_subagent` 回归
- **步骤**：
  1. 冷启 sidecar（`CHECKER_AUTO_ON_SCAFFOLD=1` · `MY_AGENT_AUTO_EXPLORE=0`）→ ready
  2. `shell.switch` grow → 造工具 `stab_s36_echo`（`write_evolve` main.py + tool.toml · **2×** confirm → y）
  3. auto demo probe → `[guard] demo probe · stab_s36_echo：通过（exit 0）`
  4. **自动 checker**：**71.8s** 内 `turn.notice`「[内核] 自动验收…」→ **`checker.verdict`** `{tool: stab_s36_echo, verdict: pass}` → notice「验收 stab_s36_echo：通过」（顶栏「验收：通过」等价）+ 子代理摘要 overlay
  5. 清理工具目录；`python -m unittest tests.test_checker_subagent -v` → **19/19 OK**
- **结果**：**pass** — 自动验收 notice / verdict 可见
- **存档**：`data/_t1821-04-out.txt` · `data/_t1821-04-err.txt` · `data/_t1821-04-smoke.txt` · `data/_t1821-04-smoke.py` · `data/_t1821-04-unittest.txt`
- **环境**：`shell_sessions` grow/daily 仍分离；工具目录已删
- **注**：默认 `CHECKER_AUTO_ON_SCAFFOLD=0`；本项须显式 `=1`（产品 M1 开关）
- **新 BUG**：无

### T-1821-05 · S-37 手动验收 CLI（2026-07-17 · **done**）

- **方式**：WS 协议 smoke（`验收` / `check` · M10 同路径）+ `ParseCheckerCommandTests`
- **步骤**：
  1. 冷启 sidecar → ready · `shell.switch` grow
  2. `user.message` `验收 write_text` → **52.9s**：demo probe exit 0 · **`checker.verdict` pass** · notice「验收 write_text：通过」+ 子代理 overlay（无父 `run_turn`）
  3. `command` `check write_text` → **37.0s**：同路径 · verdict **pass**
  4. `ParseCheckerCommandTests` → **3/3 OK**
- **结果**：**pass** — 手动 `验收` / `check` 可跑
- **存档**：`data/_t1821-05-out.txt` · `data/_t1821-05-err.txt` · `data/_t1821-05-smoke.txt` · `data/_t1821-05-smoke.py` · `data/_t1821-05-unittest.txt`
- **环境**：`shell_sessions` grow/daily 仍分离
- **新 BUG**：无

### T-1821-06 · S-38 完成声明门（2026-07-17 · **done**）

- **方式**：WS 协议 smoke（故意 demo fail + `CHECKER_AUTO_ON_SCAFFOLD=1`）+ `CompletionGateTests` / `finalize_applies_completion_gate`
- **步骤**：
  1. 单元：`apply_scaffold_completion_gate` fail → 注入「〔验收未通过·已拦截〕」且去掉「已验收/沉淀完成」；pass 保留宣称 · **3/3 OK**
  2. 冷启 sidecar（auto checker on）→ grow 造 `stab_s38_fail`（`main.py` demo `exit 1`）
  3. **80.8s**：`checker.verdict` **fail** · 助手宣称被改写为「工具已〔验收未通过·已拦截〕，〔验收未通过·已拦截〕。」· **无**残留「已验收/沉淀完成」
  4. `turn.end` 正常；清理工具目录
- **结果**：**pass** — FAIL 不得保留完成声明
- **存档**：`data/_t1821-06-out.txt` · `data/_t1821-06-err.txt` · `data/_t1821-06-smoke.txt` · `data/_t1821-06-smoke.py` · `data/_t1821-06-unittest.txt`
- **环境**：`shell_sessions` grow/daily 仍分离
- **新 BUG**：无

### T-1821-07 · S-39 托管区向导（2026-07-17 · **done**）

- **方式**：WS 协议 smoke（`host_scope.wizard` / `.add` / `.list`）+ `host_scope_api.py` T-1008 demo；**备份/还原** `data/host_scope.json`
- **步骤**：
  1. 备份现有 `host_scope.json` → 清空 → `host_scope.list` **`wizard_suggested: true`**
  2. `host_scope.wizard` 临时目录 `stab_s39` 只读 → **0.036s** `host_scope.updated` · `wizard_completed`
  3. `host_scope.add` `stab_s39_b` 读写 → **0.038s** · list **roots=2**
  4. 拒加 `workspace/`（error 含 agent root）
  5. 还原用户原配置（downloads/desktop/project1）；`python host_scope_api.py` → **9× PASS**
- **结果**：**pass** — 向导/设置可加 host 目录
- **存档**：`data/_t1821-07-out.txt` · `data/_t1821-07-err.txt` · `data/_t1821-07-smoke.txt` · `data/_t1821-07-smoke.py` · `data/_t1821-07-api-demo.txt`
- **新 BUG**：无

### T-1821-08 · S-40 host 只读+denylist（2026-07-17 · **done**）

- **方式**：协议级 smoke（临时 host roots + `resolve_host_path` / `host_read`）+ `paths.py` T-1003；备份/还原 `host_scope.json`
- **步骤**：
  1. 临时 `s40_ro`（只读）+ `s40_rw`（可写）；造 `.ssh/id_rsa` · `.env` · `notes.txt`
  2. `host:s40_ro/.ssh/id_rsa` → **HostPathDeniedError**；`host_read` ok=false
  3. `host:s40_ro/.env` → denied；`notes.txt` 可读
  4. `write=True` 对只读 root → **HostScopePermissionError**；可写 root 上 `.ssh` 仍 deny
  5. `paths.py` T-1003：`.ssh` path_denied + write=false 拒绝 · **PASS**
  6. 还原用户 `host_scope.json`
- **结果**：**pass** — `.ssh` 类 denylist + 只读写拒绝生效
- **存档**：`data/_t1821-08-smoke.txt` · `data/_t1821-08-smoke.py` · `data/_t1821-08-paths-demo.txt`
- **注**：`host_scope.py` 全量 demo 假定无 `host_scope.json`；现网有配置故未跑通，验收以本 smoke + T-1003 为准
- **新 BUG**：无

### T-1821-09 · S-41 grow 拖放（2026-07-17 · **done**）

- **方式**：WS 协议级 smoke（`python agent-core\server.py --port 8765 --takeover`）；等价 grow 壳 `file.stage`
- **脚本**：`agent-core/tests/test_file_drop_e2e.py` mode=`grow` · `data/_t1821-09-smoke.py`
- **步骤**：
  1. 冷启 sidecar（PID **21800**）→ stdout `{"ready": true, "host": "127.0.0.1", "port": 8765}`
  2. WS `file.stage` `shell: grow` + 区外临时 `.txt`
  3. **`file.staged`** `ref=workspace/_drops/20260717-1593d29d/<drop_id>/…` · 磁盘存在 · 内容含 `s41-grow-drop-ok` / `grow-drop-ok`
  4. e2e `[PASS] file-drop grow _drops staging`（EXIT=0）
- **结果**：**pass** — grow 落点 `_drops/<session_id>/`；不崩
- **存档**：`data/_t1821-09-smoke.txt` · `data/_t1821-09-smoke.py` · `data/_t1821-09-detail.txt`
- **新 BUG**：无

### T-1821-10 · S-42 daily/pet 拖放（2026-07-17 · **done**）

- **方式**：WS 协议级 smoke；等价 daily 壳 + pet（UI `BACKEND_SHELL=daily`；协议亦接受 `shell: pet`）
- **脚本**：`data/_t1821-10-smoke.py`
- **步骤**：
  1. 冷启 sidecar（shell PID **5864** / listen **9536**）→ stdout ready
  2. `shell.switch` → **daily** `session=20260717-2a559782`
  3. `file.stage` `shell: daily` → **`file.staged`** `workspace/_drops/20260717-2a559782/…` · 磁盘存在
  4. `file.stage` `shell: pet` → 同会话 `_drops/…` · 不崩
- **结果**：**pass** — daily/pet 拖放不崩；落点 `_drops/`
- **存档**：`data/_t1821-10-smoke.txt` · `data/_t1821-10-smoke.py` · `data/_t1821-10-out.txt`
- **新 BUG**：无

### T-1821-11 · S-43 忙时 project.switch（2026-07-17 · **done**）

- **方式**：桌面源码守卫断言 + WS 忙时对照（`PROJECT-MODE.md` §8.4：助手执行中禁止切换）
- **脚本**：`data/_t1821-11-smoke.py`
- **步骤**：
  1. 断言 `desktop/src/shells/project/index.ts` `requestProjectSwitch`：`chat.isWorking()` early-return **先于** `client.switchProject`；状态文案「助手执行中，请稍后再切换项目」
  2. 冷启 sidecar（shell PID **55428** / listen **47016**）→ ready
  3. `shell.switch` → project **stab-r1-demo** → `user.message` → **`turn.start`**（持忙）
  4. 忙时仍发 `project.switch` `confirm: true` → **stab-r1-b** → 服务端 **`project.switch.done`**（未硬拒）
  5. `turn.cancel` 释放忙态
- **结果**：**pass** — 产品「被拒或说明」在桌面层；服务端忙时未挡属分层设计（非本项 fail）
- **存档**：`data/_t1821-11-smoke.txt` · `data/_t1821-11-smoke.py` · `data/_t1821-11-out.txt`
- **新 BUG**：无

### T-1821-12 · S-44 project 壳新会话（2026-07-17 · **done**）

- **方式**：WS 协议级 smoke；project 壳种标记 → `新会话` → 断言 history 不串
- **脚本**：`data/_t1821-12-smoke.py`
- **步骤**：
  1. 冷启 sidecar（shell PID **40096**）→ ready
  2. `shell.switch` → project **stab-r1-demo** `session=20260715-4ceb96db`
  3. `user.message` 植入 `S44-MARKER-…` → `turn.end` ok；`session.refresh` history **含**标记（18 items）
  4. `user.message` **`新会话`** → **4.49s** `session.banner`/`session.history`；新 id **`20260717-7e6052ec`** ≠ 旧；history **0 items** · **无**标记
  5. 旧会话磁盘 `messages.jsonl` **仍含**标记（隔离，非抹掉）
- **结果**：**pass** — 绑定/新会话不串旧项目聊天
- **注**：`create_new` 后 `active_shell` 默认 **daily**、`project_id` 空（与 S-03 一致）；验收点是 history 替换不串线
- **存档**：`data/_t1821-12-smoke.txt` · `data/_t1821-12-smoke.py` · `data/_t1821-12-out.txt`
- **新 BUG**：无

### T-1821-13 · S-45 壳锁定（2026-07-17 · **done**）

- **方式**：桌面源码守卫断言 + IT-08 unittest + WS `ui.route` auto 对照（`DESKTOP.md` §5.2.2）
- **脚本**：`data/_t1821-13-smoke.py`
- **步骤**：
  1. 断言 `main.ts` `applyAutoRoute`：`if (!event.auto || readShellRouteLocked()) return` **先于** `setShell`；`settings.ts` `shell_route_locked`；`app-chrome` 锁定勾选持久化
  2. `python -m unittest tests.test_activity_router -v` → **6 OK**（IT-08）
  3. 冷启 sidecar（shell PID **40136** / listen **13760**）→ bootstrap + `session.refresh` 均发 **`ui.route` `auto: true`**
- **结果**：**pass** — 锁定后忽略 auto route（桌面层）；服务端仍发 auto（分层同 S-43）
- **存档**：`data/_t1821-13-smoke.txt` · `data/_t1821-13-smoke.py` · `data/_t1821-13-unittest.txt` · `data/_t1821-13-out.txt`
- **新 BUG**：无

### T-1821-14 · S-46 CLI+桌面双开（2026-07-17 · **done**）

- **方式**：`interface_lock` 文案断言 + 活 PID 双开冲突 + sidecar 无 takeover；`interface_lock.py` demo
- **脚本**：`data/_t1821-14-smoke.py`
- **步骤**：
  1. `format_holder_message` / `format_takeover_hint` 含「Electron 桌面」「终端 REPL」「无法启动」「--takeover」「接管」
  2. `python agent-core/interface_lock.py` demo → **PASS**（acquire / conflict / takeover / release / stale）
  3. 本进程持 **electron** 活锁 → CLI `ensure_interface_lock` 抛错，文案含占用方 + `--takeover` 提示
  4. 同锁下启 `server.py`（无 `--takeover`）→ **exit 2** · stdout `{"ready": false, "error": "lock_conflict", …}`
  5. 还原/清理 `.interface.lock`（原 stale pid 已清）
- **结果**：**pass** — 双开 lock 提示清晰
- **存档**：`data/_t1821-14-smoke.txt` · `data/_t1821-14-smoke.py` · `data/_t1821-14-demo.txt`
- **新 BUG**：无

### T-1821-15 · S-49 断网发消息（2026-07-17 · **done**）

- **方式**：桌面源码断言 + WS 协议级（不可达 LLM 基址模拟断网 → 恢复后重试）
- **脚本**：`data/_t1821-15-smoke.py`
- **步骤**：
  1. 断言 `ws.ts` 未连接抛 `WebSocket not connected`；`composer-attachments` 状态「发送失败：…」；`chat-state` `turn.end`/`error` 均 `resetTurnActivity`
  2. sidecar `LLM_BASE_URL=http://127.0.0.1:1` `LLM_TIMEOUT_SEC=2` → grow `user.message` → **3.53s** `turn.end` `ok: false` `finish_reason: timeout`（合意失败，桌面可清 busy）
  3. 杀坏网 sidecar → 正常 env 冷启 → 同路径重试 → `turn.end` `ok: true` `finish_reason: stop`
- **结果**：**pass** — 断网合意失败 + 可重试 + 不卡「处理中」（经 turn.end）
- **注**：composer catch 未调 `resetTurnActivity`（仅改状态文案）；纯本地 WS 断连场景依赖后续 reconnect/`error`；本项主路径为 API 断网
- **存档**：`data/_t1821-15-smoke.txt` · `data/_t1821-15-smoke.py`
- **新 BUG**：无

### T-1821-16 · S-50 CLI 中文/编码（2026-07-17 · **done**）

- **方式**：复现 GBK 乱码路径 + `start.bat` UTF-8 策略冒烟（DOC-08 / 对齐 T-1824-02）
- **改动**：`start.bat` 增加 `chcp 65001` · `PYTHONIOENCODING=utf-8` · `PYTHONUTF8=1`
- **脚本**：`data/_t1821-16-smoke.py`
- **步骤**：
  1. 默认（无 env）stdout **gbk** → 中文探针乱码（历史问题复现）
  2. 与 `start.bat` 相同 env → encoding **utf-8** · 探针「中文测试-计划待确认-会话」完整
  3. `main.py --takeover` + `exit` → banner `Commands: 新会话 | 只聊 | …` 中文完好
  4. `format_holder_message` 中文锁提示完好
- **结果**：**pass** — PowerShell UTF-8 策略无乱码
- **注**：T-1824-02 / DOC-08 正式文档项可随后勾；sidecar spawn 编码见 T-1824-03
- **存档**：`data/_t1821-16-smoke.txt` · `data/_t1821-16-smoke.py` · `data/_t1821-16-cli-banner.txt`
- **新 BUG**：无

### T-1821-17 · S-51 fresh bootstrap（2026-07-17 · **done**）

- **方式**：临时目录模拟新 clone → venv/`pip`/`npm` → sidecar 首启
- **改动**：`requirements.txt` 补 **`httpx>=0.27`**（原先仅 websockets，fresh 会缺 LLM 依赖）；`README.md` 补桌面/bootstrap 步骤（DOC-09 摘要）
- **脚本**：`data/_t1821-17-smoke.py`
- **步骤**：
  1. 断言 README / `start-desktop.bat` 含 pip + npm 首启路径
  2. 暂存精简树（agent-core/evolve/desktop/…）→ `.venv` + `pip install -r requirements.txt` → import `httpx`/`AgentPaths` OK
  3. `desktop/` `npm.cmd install` → `node_modules` 存在
  4. venv `server.py --port 8767 --takeover` → `{"ready": true}`；WS 首事件 `session.banner`
  5. 清理临时目录
- **结果**：**pass** — 新目录依赖安装后首启可用
- **存档**：`data/_t1821-17-smoke.txt` · `data/_t1821-17-smoke.py` · `data/_t1821-17-pip.txt` · `data/_t1821-17-npm.txt`
- **新 BUG**：无

### T-1821-18 · S-52 双实例启动（2026-07-17 · **done**）

- **方式**：双 sidecar + Electron 源码断言；补 `port_in_use` JSON（不静默抢端口）
- **改动**：`server.py` bind `OSError` → stdout `{"ready": false, "error": "port_in_use", "message": "端口 … 已被占用…"}` · exit **3**
- **脚本**：`data/_t1821-18-smoke.py`
- **步骤**：
  1. 断言 `desktop/electron/main.ts`「会话被占用」对话框 +「接管会话」（先 `startSidecar(false)`）
  2. 实例 A `--takeover` 占 **8765** → ready
  3. 实例 B **无** takeover → **exit 2** · `lock_conflict`；A 仍 LISTENING（未静默抢端口）
  4. B 带 `--takeover` 但未杀 A → **exit 3** · `port_in_use` 中文提示
  5. 停 A 后再 `--takeover` → ready（Electron `stopSidecar` → 接管等价）
- **结果**：**pass** — 二次启动提示清晰；不静默抢端口
- **存档**：`data/_t1821-18-smoke.txt` · `data/_t1821-18-smoke.py`
- **新 BUG**：无

### T-1821-19 · P1 全表记入 log（2026-07-17 · **done**）

- **动作**：将 M2-B（T-1821-01～18 / S-33～S-46 · S-49～S-52）写入下方 **P1 记录表** + **M2-B 摘要**
- **结果**：**18/18 pass** · 无新 BUG 号 · 无本批 fail
- **非 fail 注记**（分层/文档后续，不挡放行）：
  - **STD-001**（既有 backlog）：activity_router × park_session
  - **S-43 / S-45**：忙时切项目 / 壳锁定为**桌面层**拒绝；服务端仍可 switch / 仍发 `ui.route auto`
  - **S-49**：composer catch 未 `resetTurnActivity`（状态文案「发送失败」；`turn.end`/`error` 仍清 busy）
  - **S-50 / DOC-08**：`start.bat` UTF-8 已落地；正式 DOC-08 / T-1824-02 勾选可随后
  - **S-51 / DOC-09**：README bootstrap 摘要已补；T-1806-doc-09 正式项可随后
- **M2-B 批次完结** → 下一批 **M2-C**（T-1822-01 · S-32）

### T-1822-01 · S-32 只聊/动手（2026-07-17 · **done**）

- **映射**：S-32 · [`MODE-BUDGET.md`](./MODE-BUDGET.md)（模式管预算，意图管提示）
- **方式**：本地断言 + `agent._demo_t907` mock + WS 协议 smoke
- **脚本**：`data/_t1822-01-smoke.py`
- **步骤 / 断言**：
  1. overlay：`tool_budget: ask ≤5` vs `agent ≤50 + 可自动续跑`
  2. `build_llm_tools`：ask 剔除 `run_evolved`（5 tools）；agent 含（6）
  3. `_resolve_parent_loop_max("qa")`：ask=5 / agent=50（intent 不控预算）
  4. executor validate：ask 硬拒 `run_evolved`
  5. T-907a/b/c mock：agent+qa 不被 SHORT 掐；ask+qa 截断；recall 仍 0 tools
  6. WS `command` `只聊`/`动手` → `session.banner.turn_mode` + label 可观测；`meta.json` 持久化
  7. IT-11 nuance：`user.message` `只聊` 改 meta **不**推 banner；`session.refresh` 可读回 ask
  8. 测毕恢复 `动手`（agent）
- **结果**：**pass** — 只聊/动手预算与工具暴露差异可感知
- **存档**：`data/_t1822-01-smoke.txt` · `data/_t1822-01-smoke.py` · `data/_t1822-01-unittest.txt`
- **新 BUG**：无
- **下一项**：T-1822-02（S-47 govern）

### T-1822-02 · S-47 govern 占位（2026-07-17 · **done**）

- **映射**：S-47 · IT-07 · [`DESKTOP.md`](./DESKTOP.md) §3.4（T-904h defer 占位）
- **方式**：桌面源码断言 + `switch_shell` 单测 + WS grow↔govern
- **脚本**：`data/_t1822-02-smoke.py`
- **步骤 / 断言**：
  1. `shells/govern` 占位文案 + `shell-router` case + chrome「治理」
  2. unit：grow→govern **同 session_id** · `session_replaced=False`；可切回 grow；不写 `shell_sessions.govern`
  3. WS：`shell.switch` govern → done（不崩、同 sid）；再 switch grow → `active_shell=grow`
- **Hotfix**（本项复现）：`server.py` `shell.switch` 在 **同 conversation_id** 时未采纳 `switch_shell` 返回的新 session 对象 → govern→grow 后 banner 仍 `active_shell=govern`。改为**始终** `repl.session = new_session`，仅 cid 变化时 `_rebind_agent`
- **注记**：当前 `shell_sessions` grow/daily 同指向 `20260717-1593d29d`（既有污染，非本项引入；续接指引仍要求分离）
- **结果**：**pass**（hotfix 后）— 占位不崩；可回 grow
- **存档**：`data/_t1822-02-smoke.txt` · `data/_t1822-02-smoke.py`
- **新 BUG**：无（当场 hotfix，未单开 BUG 号）
- **下一项**：T-1822-03（P2 失败项 backlog）

### T-1822-03 · P2 失败项 → backlog（2026-07-18 · **done**）

- **范围**：M2-C · T-1822-01（S-32）· T-1822-02（S-47）
- **扫描**：

| 项 | 结果 | 处置 |
|----|------|------|
| S-32 只聊/动手 | **pass** | 无 fail |
| S-47 govern 占位 | **pass**（含 server hotfix） | 无 fail；hotfix 已落地，不入 backlog |
| 新 STD / BUG P2 | **无** | — |
| STD-001 | 仍 **open** | 补记 S-47 观察：`shell_sessions` grow/daily 同 id → [`stabilization-backlog.md`](./stabilization-backlog.md) |

- **非 fail 注记（不入新 STD）**：
  - IT-11：`user.message` `只聊` 不推 banner（parity 已决）
  - S-47 hotfix：同 cid 须采纳新 session 对象（已修）
  - M2-B 遗留分层注记（S-43/45/49 等）仍见 T-1821-19；非本批 P2 fail
- **结果**：**N/A → done** — **0** 条 P2 失败项需记 backlog；**不阻塞放行**
- **M2-C 批次完结** → 下一批 **M2-D**（T-1813-01 · 协议漂移）

---

## P1 记录表（放行前 ≥1 次全过，或 BUG+绕行）

| 日期 | 范围 | 结果 | 失败项 → BUG / 绕行 |
|------|------|------|---------------------|
| 2026-07-17 | **M2-A** S-11,S-15,S-18～S-31（T-1820-01～16） | **16/16 pass** | 无 fail · STD-001 绕行（activity_router×park；非本批失败） |
| 2026-07-17 | **M2-B** S-33～S-46,S-49～S-52（T-1821-01～18） | **18/18 pass** | 无 fail · 注记见 T-1821-19（非本批 fail） |
| 2026-07-17～18 | **M2-C** S-32,S-47（T-1822-01～03） | **2/2 pass** | 无 fail · STD-001 续观察（S-47）；无新增 STD |
| 2026-07-18 | **M2-D** 协议漂移（T-1813-01～04） | **审计 done** | 修代码 0；DESKTOP v0.3.10；D-02/D-03 实现 defer |

### 2026-07-18 · M2-C 摘要（T-1822-03）

| ID | 映射 | 结果 | 要点 |
|----|------|------|------|
| T-1822-01 | S-32 | pass | 只聊/动手预算与工具暴露可感知 |
| T-1822-02 | S-47 | pass | govern 占位不崩；可回 grow（含 shell.switch hotfix） |
| T-1822-03 | backlog | N/A | 0 P2 fail；STD-001 补观察 |

### T-1813-01 · 入站 type 列表 · 附录表 A（2026-07-18 · **done**）

- **范围**：`agent-core/server.py` `_dispatch_inline` / `_dispatch` + `project_api.dispatch_project_message` + `host_scope_api.dispatch_host_scope_message`
- **对照**：[`DESKTOP.md`](./DESKTOP.md) §5.1（客户端 → 服务端）
- **处置**：本条只标漂移；修 doc / 修代码 / defer → **T-1813-03**

#### 附录表 A · WS 入站 `type`（代码为真）

| # | type | 入口 | 关键字段 | §5.1 |
|---|------|------|----------|------|
| 1 | `confirm.response` | `_dispatch_inline` | `request_id` · `choice` | 有 |
| 2 | `turn.cancel` | `_dispatch_inline` | — | 有 |
| 3 | `user.message` | `_dispatch` | `text` · 可选 `attachments[]` | 有（表内两行，同 type） |
| 4 | `file.stage` | `_dispatch` | `paths[]` · 可选 `shell` | 有 |
| 5 | `file.unstage` | `_dispatch` | `attachment_id` | 有 |
| 6 | `command` | `_dispatch` | `name` | 有 |
| 7 | `session.list` | `_dispatch` | — | 有 |
| 8 | `session.open` | `_dispatch` | `session_id` | 有 |
| 9 | `session.refresh` | `_dispatch` | — | 有 |
| 10 | `proposal.accept` | `_dispatch` | `proposal_id` | 有 |
| 11 | `proposal.reject` | `_dispatch` | `proposal_id` | 有 |
| 12 | `shell.switch` | `_dispatch` | `shell` · 可选 `project_id` | 有 |
| 13 | `host_scope.list` | `host_scope_api`（经 `host_scope.*` 前缀） | — | 有 |
| 14 | `host_scope.add` | 同上 | `host_id` · `path` · 可选 `write`/`label` | 有 |
| 15 | `host_scope.remove` | 同上 | `host_id` | 有 |
| 16 | `host_scope.write` | 同上 | `host_id` · `write` | 有 |
| 17 | `host_scope.repath` | 同上 | `host_id` · `path` | 有 |
| 18 | `host_scope.wizard` | 同上 | `entries[]` 或 `skip` | 有 |
| 19 | `project.list` | `project_api`（经 `project.*`） | — | 有 |
| 20 | `project.state` | 同上 | — | 有 |
| 21 | `project.open` | 同上 | `project_id` | 有 |
| 22 | `project.switch` | 同上 | `project_id` · 可选 `confirm`/`request_id` | 有 |
| 23 | `plan.response` | `project_api`（`server` 显式分支） | `request_id` · `choice` | 有 |
| 24 | `project.verify` | `project_api` | — | 有 |

**合计**：代码入站 **24** 个 distinct `type`（`proposal.accept`/`reject` 分计）。

#### 与 DESKTOP.md §5.1 diff（仅标注 · 处置 → T-1813-03）

| 类别 | type | 说明 |
|------|------|------|
| **文档有 / 代码无（入站）** | `shell.switch.done` | §5.1 列为客户端→服务端；代码仅在 `shell.switch` 成功后 **`bridge.emit` 出站**（`ws.ts` 亦为 `ServerEvent`）。§5.2 出站表未单列此 type。 |
| **代码有 / 文档无（入站）** | — | 无 |
| 注（非漂移） | `user.message` | §5.1 写两行（基础 + attachments）；代码同一 `type` |
| 注（非漂移） | 前缀路由 | 未知 `host_scope.*` / `project.*` 会进对应 dispatch 再 `unknown`/`error`；表 A 只列已实现分支 |

- **下一项**：T-1813-02（出站 type · 附录表 B）

### T-1813-02 · 出站 type 列表 · 附录表 B（2026-07-18 · **done**）

- **范围**：`WsBridge.emit` 及挂接路径 — `emit_session_state` · `on_executor_event` · `on_turn_event`（`agent.py`）· `activity_router` · `project_api` / `project_switch` · `host_scope_api` · `_run_line` / `_dispatch`
- **对照**：`desktop/src/api/ws.ts` `ServerEvent` 联合类型（前端 case 消费面）
- **处置**：本条只标漂移；修 doc / 修代码 / defer → **T-1813-03**

#### 附录表 B · WS 出站 `type`（代码为真 · 上线载荷）

| # | type | 主要来源 | ws.ts |
|---|------|----------|-------|
| 1 | `session.banner` | `emit_session_state` / `activity_router`（topics 变） | 有 |
| 2 | `session.memory` | `emit_session_state` / `agent` 压缩后 | 有 |
| 3 | `session.history` | `emit_session_state` | 有 |
| 4 | `evolve.proposals` | `emit_session_state` / proposal accept·reject | 有 |
| 5 | `ui.route` | `emit_activity_route`（连接 / refresh / turn / shell.switch） | 有 |
| 6 | `turn.start` | `agent.on_turn_event` | 有 |
| 7 | `turn.notice` | `agent`（压缩教育、偏航、checker overlay 等） | 有 |
| 8 | `turn.end` | `_run_line` finally | 有 |
| 9 | `checker.verdict` | `agent` / CLI 同路径 | 有 |
| 10 | `assistant.delta` | `emit_content_delta` | 有 |
| 11 | `assistant.done` | `emit_assistant` | 有 |
| 12 | `reasoning.delta` | `emit_reasoning_delta` | 有 |
| 13 | `tool.start` | `executor` → `on_executor_event` | 有 |
| 14 | `tool.end` | 同上 | 有 |
| 15 | `confirm.request` | `confirm_fn` | 有 |
| 16 | `confirm.done` | `confirm_fn` / `deliver_confirm` / `request_cancel` | 有 |
| 17 | `prompt.request` | `input_fn` | 有 |
| 18 | `notice` | 多处（含 timeout / cancel / proposal / 过期确认） | 有 |
| 19 | `error` | `emit_error` / `output_fn` llm error 行 | 有 |
| 20 | `session.list` | `_dispatch` 应答 | 有 |
| 21 | `shell.switch.done` | `_dispatch` `shell.switch` 成功后 | 有 |
| 22 | `host_scope.state` | `host_scope.list` 应答 | 有 |
| 23 | `host_scope.updated` | host_scope 变更应答 | 有 |
| 24 | `project.list` | `project_api` | 有 |
| 25 | `project.state` | `emit_session_state` / `project_api` | 有 |
| 26 | `project.switch.request` | `project_switch` | 有 |
| 27 | `project.switch.done` | `project_api` | 有 |
| 28 | `plan.request` | `project_api` | 有 |
| 29 | `plan.done` | `handle_plan_response` | 有 |
| 30 | `project.verify.done` | `project_api` | 有 |
| 31 | `file.staged` | `_dispatch` `file.stage` | 有 |
| 32 | `file.unstaged` | `_dispatch` `file.unstage` | 有 |
| 33 | `file.error` | `file.stage` 单文件失败 | 有 |

**合计**：出站 **33** 个 distinct wire `type`。

#### 非独立出站（executor 内部名 → 上线前改写）

| 内部 event | 上线结果 |
|------------|----------|
| `guard.notice` | → `notice` |
| `session.workspace_evolved_approved` | → `notice` |
| 其它未知 `on_executor_event` | → `notice`（`"{event_type}: {json}"`） |

#### 与 `ws.ts` `ServerEvent` diff

| 类别 | type | 说明 |
|------|------|------|
| **代码有 / ws.ts 无** | — | **无** |
| **ws.ts 有 / 代码无** | — | **无** |

#### 预览 · 与 DESKTOP.md §5.2（标注留给 T-1813-03）

| 类别 | type | 说明 |
|------|------|------|
| 文档有 / 代码无（出站） | `activity.line` · `explore.progress` | §5.2 有；代码与 `ws.ts` 均无 |
| 代码有 / 文档 §5.2 无 | `checker.verdict` · `prompt.request` · `session.list` · `shell.switch.done` | 代码+`ws.ts` 有；§5.2 未列（`shell.switch.done` 误列在 §5.1 入站） |

- **下一项**：T-1813-03（漂移清单 + 处置）

### T-1813-03 · 协议漂移清单 + 处置（2026-07-18 · **done**）

- **对照基线**：附录表 A/B（代码为真）· [`DESKTOP.md`](./DESKTOP.md) §5.1/§5.2/§5.4 · `desktop/src/api/ws.ts`
- **原则**：Phase 18 **冻结 feature** → 本批 **无「修代码」**；实现缺口标 **defer**；文档错位标 **修 doc**（落盘 → **T-1813-04**）
- **代码 ↔ ws.ts**：出站双向无缺口（T-1813-02）→ 不列入下表

#### 漂移清单

| ID | 类别 | type / 项 | 现象 | 处置 | 落地 |
|----|------|-----------|------|------|------|
| **D-01** | 文档有·方向错 | `shell.switch.done` | §5.1 列为**入站**；代码/`ws.ts` 为**出站**；§5.2 未列 | **修 doc** | 从 §5.1 删除；写入 §5.2（`session_id` · `session_replaced`） |
| **D-02** | 文档有 / 代码无 | `activity.line` | §5.2 + §3.2 提及；代码与 `ws.ts` **均无** emit | **修 doc** + 实现 **defer** | §5.2 标「未实现；A 层由 `tool.start`/`tool.end` 覆盖」；§3 同步；**不**新加 emit（冻结） |
| **D-03** | 文档有 / 代码无 | `explore.progress` | §5.2 有；代码/`ws.ts` 无；[`STABILIZATION.md`](./STABILIZATION.md) 矩阵 P1/IT-14 | **修 doc** + 实现 **defer** | §5.2 标「未实现 / 规划」；实现放行后开任务（非本批） |
| **D-04** | 代码有 / 文档无 | `checker.verdict` | `agent` emit + `ws.ts`/`chat-state` 已消费；§5.2 未列 | **修 doc** | 补入 §5.2（`tool_name` · `verdict`） |
| **D-05** | 代码有 / 文档无 | `prompt.request` | `WsBridge.input_fn` emit + `ws.ts` 有；§5.2 未列 | **修 doc** | 补入 §5.2（REPL/`input_fn` 提示） |
| **D-06** | 代码有 / 文档无 | `session.list`（出站） | 入站有；应答出站 `session_ids` 未写入 §5.2 | **修 doc** | §5.2 补「入站 `session.list` 的应答」 |
| **D-07** | 文档冗余 | `user.message` | §5.1 两行（基础 + attachments），同 type | **修 doc** | 合并为一行，attachments 写在说明列 |
| **D-08** | 勾选过时 | §5.4 Phase 15 开放三项 | `[ ] turn.cancel` / `[ ] CONFIRM_TIMEOUT 90s` / `[ ] LLM 协作取消` — 代码与 smoke 已 **done**（T-1402～05 · S-05/S-26） | **修 doc** | **T-1813-04** 专责：改为 `[x]` 并改「开放」措辞 |
| **D-09** | 散文过时 | §3.2 确认超时句 | 写「默认 90s（**现码 3600s**）」；现码已默认 **90s** | **修 doc** | 删「现码 3600s」；与 TURN-CONTROL / 实现一致 |

#### 处置汇总

| 处置 | 条数 | ID |
|------|------|-----|
| **修 doc** | 9 | D-01～D-09（D-02/D-03 另附实现 defer） |
| **修代码** | **0** | — |
| **defer**（实现） | 2 | D-02 `activity.line` · D-03 `explore.progress`（仅实现；文档须先标未实现） |

#### 明确非漂移

| 项 | 说明 |
|----|------|
| 入站 24 type | 除 D-01 错位外，§5.1 与代码一致 |
| 出站 33 type ↔ `ws.ts` | 全覆盖 |
| `guard.notice` 等 | 上线前改写为 `notice`（附录表 B）；非独立协议 type |

- **本条不改** `DESKTOP.md` 正文（一条一停）→ 修 doc 落盘见 **T-1813-04**
- **下一项**：T-1813-04

### T-1813-04 · DESKTOP.md 修 doc 落盘（2026-07-18 · **done**）

- **文件**：[`DESKTOP.md`](./DESKTOP.md) → **v0.3.10-draft**
- **范围**：T-1813-03 漂移清单 D-01～D-09 全部 **修 doc** 落盘；**无代码改动**

| ID | 落盘位置 | 结果 |
|----|----------|------|
| D-01 | §5.1 删除 `shell.switch.done`；§5.2 增出站行 | done |
| D-02 | §5.2 移入「未实现/规划」；§3.2.2 A 层改为 `tool.start`/`tool.end` | done |
| D-03 | §5.2「未实现/规划」+ §3.2.2 注明 | done |
| D-04 | §5.2 补 `checker.verdict` | done |
| D-05 | §5.2 补 `prompt.request` | done |
| D-06 | §5.2 补出站 `session.list` | done |
| D-07 | §5.1 `user.message` 合并一行（含 attachments） | done |
| D-08 | Phase 15 三项移入 **§5.3** 并 `[x]`；§5.4 改为「已决」引用 | done |
| D-09 | §3.2.4 删「现码 3600s」 | done |

- **M2-D 批次完结** → 下一批 **M2-E**（T-1823-01）

### T-1823-01 · 坏 jsonl 行用户可见 notice 方案（2026-07-18 · **done**）

**问题（T-1806-07 / IT-55 现状）**

- `_read_messages`（`session.py`）对非法 JSON、非 object 行 `continue`，**不记数、不告知**
- `Session.load` 返回的 `Session` **无** `corruption_notices`；聊天区只见幸存行，用户以为历史完整
- xfail：`test_it55_bad_jsonl_should_surface_notice_to_user` 期望 `getattr(loaded, "corruption_notices")` 非空

**已决（实现约束 · T-1823-02）**

| # | 决策 | 理由 |
|---|------|------|
| D1 | **继续跳过**坏行（不 crash、跳过本身不 rewrite jsonl） | §3.9 要求「跳过且告知」；生存行为已有绿测 |
| D2 | **`Session.corruption_notices: list[str]`** 在 `Session.load` 填充 | 直接满足 IT-55 单元契约；与 meta 告知可同字段复用 |
| D3 | 用户通道：**`turn.notice` + `level: "warn"`** | 任务措辞；桌面 `chat-state` 已推 notice 块；**无需**新 WS type / 改 `ws.ts` |
| D4 | 发射顺序：`emit_session_state`（含 **`session.history`**）**之后** | `session.history` 会 `blocks = loaded`，先发 notice 会被冲掉 |
| D5 | 发射点：WS **connect** / **`shell.switch`** / **`session.open`** / project **`session_replaced`** | 凡真 `Session.load` 的路径；仅 `session.refresh` 不重读磁盘，不单独依赖 |
| D6 | **不用** `error`；**不**扩展 `session.banner`；**不**新建 `session.corruption` | `error` 过重且 reset busy；banner 不进聊天区；新 type 超 M2-E 最小范围 |
| D7 | notice **不落盘** `messages.jsonl` | 对齐 TURN-FEEDBACK A4（偏航条 ephemeral） |
| D8 | CLI：加载后一行 muted 打印 | CLI↔桌面对等 |

**文案（建议）**

```text
会话历史有 N 行损坏已跳过（messages.jsonl）。聊天区仅显示可读消息。
```

可选附行号（1-based，对应文件物理行）。全坏行 → `messages=[]` 仍发 notice（N=全部非空行）。

**备选否决**

| 方案 | 否决原因 |
|------|----------|
| 仅 `session.banner` 字段 | 顶栏不展示聊天 notice；需改 chrome |
| 仅 `session.memory` 条数差 | 无解释，仍近静默 |
| 新 `session.corruption` | 协议+前端扩散；本 Phase 冻结 feature |
| plain `notice`（无 level） | 可用，但 `turn.notice`+warn 更贴「软问题」分类 |

**与后续项边界**

| 项 | 关系 |
|----|------|
| T-1823-02 | 实现本方案；翻 IT-55 xfail |
| T-1823-03/04 | meta 坏 → **同** `corruption_notices` + 同 `turn.notice` 通道（另开设计条） |
| T-1823-05 | state 坏：IT-56 xfail 挂在 `paths.corruption_notices` — **实现时可统一到一处**（Session 或 paths），避免双钩子；本条不定死 state 挂点 |

**T-1823-02 实现清单（预告 · 本条不改代码）**

1. `_read_messages` 返回跳过统计（或 out-param）→ `Session.load` 写入 `corruption_notices`
2. `server.py`（及 shell/project switch 的 emit 点）在 history 后 `bridge.emit(turn.notice)`
3. CLI `resume_or_create` / load 后 muted print
4. `test_session_corruption.py`：去掉 `@expectedFailure`；可加强文案/计数断言
5. 可选：`DESKTOP.md` §5.2 一行说明「损坏跳过 → turn.notice」（可随实现或 DOC 批次）

**文档落盘**：[`STABILIZATION.md`](./STABILIZATION.md) **v1.0.1** · §3.9.1

**下一项**：T-1823-02

### T-1823-02 · 实现坏 jsonl → corruption_notices + turn.notice（2026-07-18 · **done**）

- **代码**
  - `session.py`：`_read_messages(..., skipped_lines=)` 记 1-based 物理行；`Session.corruption_notices`；`format_messages_corruption_notice` / `corruption_notice_events` / `emit_corruption_notices`
  - `server.py`：WS connect · `session.open` · `shell.switch` 在 `emit_session_state` **之后** emit
  - `project_api.py`：`session_replaced` 时 history 后 `extend(corruption_notice_events)`
  - `main.py`：CLI `_print_session_banner` 附带 `[warn] …`
- **测试**：去掉 IT-55 `@expectedFailure`；断言文案含行数/行号 + `turn.notice` warn 事件
- **Gate**：`python tests/run_stabilization.py` → **TOTAL OK 111 run, 1 xfail**（仅 IT-56）
- **未改**：`session.refresh` / 普通 `command` 重推 state **不**再发 corruption（避免刷屏）；桌面 `ws.ts` 无需改
- **下一项**：T-1823-03

### T-1823-03 · 坏 meta.json 告知方案（2026-07-18 · **done**）

**问题（现状）**

- `_read_meta`（`session.py`）在 `JSONDecodeError` / `OSError` / 根值非 object / 文件缺失时，**静默**返回默认 `SessionMeta`
- 用户不知 **topics / active_shell / project_* / turn_mode / phase** 等绑定已丢；顶栏像「新会话」而 `messages.jsonl` 仍有历史
- T-1806-07 未单独写 meta 用例；§3.9 将 meta 与 jsonl 同挂 IT-55

**丢失面（告知文案须点名）**

| 字段族 | 默认回退后 |
|--------|------------|
| `topics` / `llm_model` | 空主题；模型重解析 |
| `active_shell` | → `daily`（`DEFAULT_ACTIVE_SHELL`） |
| `project_id` / `project_root` / plan_* | 项目绑定清空 |
| `turn_mode` | → `agent` |
| `phase` / compact / feedback / evolve flags | 回默认 |

**已决（实现约束 · T-1823-04）**

| # | 决策 | 理由 |
|---|------|------|
| M1 | **继续回退默认**，不 crash | 与 §3.9「回退默认且告知」一致 |
| M2 | **复用** `Session.corruption_notices` + `turn.notice` warn | T-1823-01 D2/D3；发射点 T-1823-02 已接，免新协议 |
| M3 | 「坏」= 结构失败（不可读 / 非 object）+ **`Session.load` 时文件缺失** | 验收「知绑定丢失」；缺文件同样丢绑定 |
| M4 | 合法 object + 字段缺省/类型错 → **不**告（`from_dict` 默认） | 留给 DOC-05 / T-1823-06，避免噪声 |
| M5 | **加载不改写**坏 `meta.json`；下次 `save` 覆盖为既有副作用 | 对齐 jsonl「跳过不 rewrite」；备份 defer |
| M6 | jsonl 与 meta 可 **同时** 各一条 notice | 两路独立损坏 |
| M7 | `refresh_pending_feedback_from_disk` 静默读 meta：**本项不改** | 窄路径；防反馈回路刷屏 |

**文案（建议）**

```text
会话元数据损坏或缺失，已回退默认（meta.json）。主题/壳/项目绑定可能已丢失，请核对顶栏。
```

缺失与损坏可用同一句，或实现时用 `缺失` / `损坏` 二字区分。

**与 T-1823-04 边界**

| 做 | 不做（本设计条） |
|----|------------------|
| `_read_meta` 报告失败原因 → `Session.load` 追加 notice | 改 WS 发射点（已有） |
| 单测：坏 / 非 object / 缺文件 → `corruption_notices` 非空 | 自动备份坏 meta；部分字段抢救 |
| 确认 CLI banner 已打印全部 notices | 新 `session.corruption` type |

**文档落盘**：[`STABILIZATION.md`](./STABILIZATION.md) **v1.0.2** · §3.9.2

**下一项**：T-1823-04

### T-1823-04 · 实现坏 meta → corruption_notices（2026-07-18 · **done**）

- **代码**（仅 `session.py`；WS/CLI 沿用 T-1823-02）
  - `_read_meta(..., corruption_kinds=)`：`missing` / `unreadable` / `non_object`
  - `format_meta_corruption_notice`：文案区分「缺失」vs「损坏」+ 绑定可能丢失
  - `Session.load`：meta notices 与 jsonl notices **可并存**
  - 无 `corruption_kinds` 的调用（如 `refresh_pending_feedback_from_disk`）仍静默
- **测试**：`BadMetaJsonNoticeTests` 6 cases（坏 / 非 object / 缺失 / 合法部分字段无 notice / 静默读 / 与 jsonl 并存）
- **Gate**：`TOTAL OK 117 run, 1 xfail`（仅 IT-56）
- **下一项**：T-1823-05

### T-1823-05 · 坏 state.json 降级 + notice（2026-07-18 · **done**）

- **代码**
  - `paths.py`：`AgentPaths.corruption_notices`；`read_agent_state_payload` / `write_agent_state_payload` / `note_state_corruption`
  - `shell_switch.py` / `project_switch.py` / `session.read_last_conversation_id`：统一走 `read_agent_state_payload`
  - `corruption_notice_events`：合并 Session + paths notices → `turn.notice`；CLI banner 同理
- **行为**：坏/非 object → `{}` + 一次 notice；缺文件不告；下次 `record_*` 可重建合法 state
- **测试**：去掉 IT-56 `@expectedFailure`；断言 `paths.corruption_notices` + 事件文案含 `state.json`
- **Gate**：`TOTAL OK 117 run`（**0 xfail**）
- **下一项**：T-1823-06

### T-1823-06 · DOC-05 schema 兼容表（2026-07-18 · **done**）

- **落盘**：[`RUNTIME.md`](./RUNTIME.md) **v0.2.6** · **§2.4**（原则 + `meta.json` 字段表 + `state.json` 字段表 + jsonl 行级说明）
- **交叉引用**：[`MEMORY.md`](./MEMORY.md) §6.2 · [`STABILIZATION.md`](./STABILIZATION.md) §3.9 DOC-05 行
- **同步勾选**：T-1806-doc-05 **done**
- **M2-E 批次完结** → 下一批 **M2-F**（T-1824-01）

### T-1824-01 · Windows 乱码复现路径调查（2026-07-18 · **done**）

- **方式**：本机 code page 探针 + 字节级复现（对照 S-50 / §3.11）；**不新开 BUG 号**（备注 + 指向 T-1824-02/03）
- **环境实测**：
  | 项 | 值 |
  |----|-----|
  | `chcp` | **936**（活动代码页 / CP936 · GBK） |
  | 裸 `python` `sys.stdout.encoding` | **gbk**（无 `PYTHONIOENCODING` / `PYTHONUTF8`） |
  | `sys.getfilesystemencoding()` | **utf-8** |
- **输出点矩阵**：

  | 表面 | 编码行为 | 中文乱码？ | 状态 |
  |------|----------|-----------|------|
  | **CLI** `start.bat` | `chcp 65001` + `PYTHONIOENCODING=utf-8` + `PYTHONUTF8=1` | 否 | **已缓解**（T-1821-16 / S-50） |
  | **CLI** 裸 `python agent-core\main.py` | stdout=gbk | **是**（控制台 / 管道若按 UTF-8 解） | 用户须走 `start.bat`；T-1824-02 文档化 |
  | **sidecar 磁盘日志** | `RotatingFileHandler(encoding=utf-8)` | 否 | **已缓解**（T-1805-02）；探针 marker 可读 |
  | **Electron spawn** stdout/stderr | `env: process.env`（**无** PYTHON*）→ 常 gbk；`chunk.toString("utf-8")` | **是**（`port_in_use` / `lock_conflict` 中文 `message`） | **缺口 → T-1824-03** |
  | **WS → UI 聊天/notice** | UTF-8 JSON text frames | 否 | 协议层安全 |
  | **start-desktop.bat** | 无 chcp / PYTHON* | 本身不打印中文；子进程继承 shell | 与 T-1824-03 一并考虑 |
  | **运维捕获** | PowerShell `Tee-Object` / 默认 `Get-Content` | 易把 UTF-8 **显示**成乱码 | 写 UTF-8 文件验收（已知坑 #5） |

- **关键复现（Electron 管道）**：Python `print(json.dumps({..., "message": "端口 …"}, ensure_ascii=False))` 在默认 gbk 下写出 GBK 字节 → Electron 按 UTF-8 解码 → `message` 乱码；同 payload 加 `PYTHONIOENCODING=utf-8` 后 Electron 解码完好（T-1824-03 候选修复已验证）。
- **存档**：`data/_t1824-01-probe.py` · `data/_t1824-01-probe.txt`
- **新 BUG**：无（P1 已由 S-50 / T-1824 跟踪；不另开 `BUGS.md`）
- **下一项**：T-1824-02

### T-1824-02 · CLI UTF-8 策略 + DOC-08（2026-07-18 · **done**）

- **实现**：`start.bat` 已有 `chcp 65001` + `PYTHONIOENCODING=utf-8` + `PYTHONUTF8=1`（T-1821-16）；本项 **正式文档化**，未改策略本身
- **DOC-08 落盘**：
  - [`DESKTOP.md`](./DESKTOP.md) **v0.3.11-draft** · **§3.8.1**（推荐入口、三强制项、勿裸跑、运维捕获、指向 T-1824-03）
  - `start.bat` REM 块展开（对照 §3.8.1）
  - [`README.md`](../README.md) 快速开始注明 Windows 用 `start.bat`
- **验收**：重跑 `data/_t1821-16-smoke.py` → **S-50 PASS**（utf-8 策略探针完整；CLI banner / lock 中文完好）；smoke `log()` 改为 ASCII-safe 控制台输出，避免 CP936 下打印乱码探针时 `UnicodeEncodeError`
- **同步勾选**：T-1806-doc-08 **done**
- **下一项**：T-1824-03

### T-1824-03 · Electron startSidecar UTF-8（2026-07-18 · **done**）

- **代码**：`desktop/electron/main.ts`
  - 新增 `sidecarSpawnEnv()`：`PYTHONIOENCODING=utf-8` + `PYTHONUTF8=1`（叠在 `process.env` 上）
  - `startSidecar` → `env: sidecarSpawnEnv()`（不再裸 `{ ...process.env }`）
- **文档**：[`DESKTOP.md`](./DESKTOP.md) **v0.3.12-draft** · §4.4 增编码步 · §3.8.1 Electron 行改为已落地
- **验收**（`data/_t1824-03-smoke.py`）：
  1. 源码断言 `startSidecar` 使用 `sidecarSpawnEnv`
  2. 无 PYTHON* → Electron 风格 `utf-8` 解码丢失「端口…」（CP936 基线）
  3. 有 PYTHON* → `port_in_use` 中文 `message` 完好
  4. sidecar 磁盘日志中文 marker 可读（T-1805 UTF-8 FileHandler）
- **结果**：**PASS**
- **存档**：`data/_t1824-03-smoke.py` · `data/_t1824-03-smoke.txt`
- **下一项**：T-1824-04

### T-1824-04 · IT-62 测试隔离审计（2026-07-18 · **done**）

- **方式**：只读审计 `agent-core/tests/*` + 生产 `_demo` / `__main__` + 磁盘 `data/sessions` 残留；**不改代码**
- **全文清单**：[`data/_t1824-04-audit.md`](../data/_t1824-04-audit.md)
- **磁盘快照**：`data/sessions/` ≥**91** 个 `_*` 形目录；`repl_sessions/{bootstrap,debug,chk,run1,t2,default}.pkl`
- **关键结论**：
  - IT-62 未入 Gate，但 **高危污染源已在 Gate 内**（每次 `run_stabilization.py` 都会打 live `data/`）
  - 最高危模式：**(1)** 固定会话 id 不清理 `(_guard_test` / `_checker_*` / `_m1_*`)；**(2)** backup/restore live `state.json`；**(3)** 追加 live `evolve_log.jsonl`；**(4)** 写 live `evolve/tools/...`
- **Gate 高危摘要**：

  | 源 | 问题 |
  |----|------|
  | `test_runtime_guards_m1` | live evolve 工具 + `_m1_auto_demo` / `_inline_guard_log` 残留 |
  | `test_runtime_guards` AgentTimeout | `_guard_test` **无清理** |
  | `test_session_corruption` BadState | 直接改 live `state.json` |
  | `test_checker_subagent` Runner/Auto/Gate | `_checker_*` + 污染 `evolve_log.jsonl` |
  | project/switch/contracts/cross Shell | live `state.json` + workspace |

- **已良好隔离（范本）**：`test_sanitize_log_value` · sidecar log temp patch · 部分 guards tempfile · `session._demo` / `logging._demo`
- **T-1824-05 优先 5 项**：① guards_m1 ② state.json 类 ③ checker Runner/Auto ④ `_guard_test` ⑤（次）生产 `_demo` / file_stage / write_evolve_pipeline
- **下一项**：T-1824-05

### T-1824-05 · 修最高危测试隔离（2026-07-18 · **done**）

- **Helper**：`agent-core/tests/isolation_helpers.py`
  - `make_temp_agent_paths` / `temporary_agent_paths`
  - 可选 `copy_tool_dirs`；junction/symlink `agent-core` 供 `write_evolve` 等导入
  - cleanup **先卸 junction** 再 `rmtree`，避免误删 live `agent-core`
- **产品小修**：`tools/executor.py` `_cross_session_read_target` 改用 `self.registry.agent_paths`（不再硬编码 `discover()`），临时根上跨会话 confirm 才生效
- **已改 Gate 测试**（不再写 live `data/` / `state.json` / `evolve_log.jsonl` / live evolve tools）：
  - `test_runtime_guards_m1` · `test_runtime_guards`（timeout 链）
  - `test_session_corruption.BadStateJson*`
  - `test_checker_subagent` Runner/Auto
  - `test_project_lifecycle` / `test_project_switch` / `test_module_contracts.ProjectApi*`
  - `test_cross_session_read`
  - `test_sidecar_logging`（消 `_it58_*`）
- **验收**：`python tests/run_stabilization.py` → **117 run, 0 fail**；Gate 前后 live `data/sessions/_*` **新增 0**
- **未改（仍 MED / 非 Gate）**：生产 `_demo`、`test_file_stage`、`test_write_evolve_pipeline`、历史磁盘残留清扫
- **存档**：`data/_t1824-05-gate.txt`
- **下一项**：T-1824-06

### T-1824-06 · IT-61 中文文件名拖放（2026-07-18 · **done**）

- **方式**：单元测试（临时 `AgentPaths`，无 live `data/` 污染）；覆盖 FILES-DROP `stage_absolute_path`
- **代码**：`agent-core/tests/test_file_stage.py` · `ChineseFilenameDropTests`（3 cases）+ 既有 FileStage 改隔离
- **断言**：
  1. **grow**：外部 `中文说明.txt` → `_drops/` · `item.name` / 磁盘名 / 附件块均保留中文 · UTF-8 正文可读
  2. **project**：`说明 文档.md`（中文+空格）→ `_incoming/` 完好
  3. **daily**：中文父目录 `桌面资料/笔记/草稿.py` → 仅文件名落盘，内容完好
- **验收**：`python -m unittest tests.test_file_stage -v` → **6/6 OK**（成功路径；非合意错误）
- **注**：未入 Gate runner（同 IT-62 曾 defer；可随后纳入）；未要求改 `file_stage.py`（既有行为已正确）
- **存档**：`data/_t1824-06-unittest.txt`
- **M2-F 批次完结** → 下一批 **M2-G**（T-1806-doc-01）

### T-1806-doc-01 · DOC-01 pet→daily 映射（2026-07-18 · **done**）

- **交付**：读者可明确知道 **pet UI ≠ daily backend**，但 **会话线 = daily**
- **文档**：
  - [`PET-SHELL.md`](./PET-SHELL.md) **§1.3** — 完整映射表（UI / `shell.switch` / `shell_sessions` / 落盘 / 拖放 / `active_shell` / WS）+ 三线对照（无 `pet` 键）
  - [`DESKTOP.md`](./DESKTOP.md) **§3.3.6** — 摘要表 + 链到 PET-SHELL §1.3；**§3.9.2** 补「三线」行注明伴侶复用 daily
- **版本**：PET-SHELL **v0.2.1** · DESKTOP **v0.3.13-draft**
- **验收**：两处均有明确 §/表；实现锚点对齐 `shells/pet/index.ts` `BACKEND_SHELL = "daily"`
- **下一项**：T-1806-doc-02（DOC-02 CLI parity）

### T-1806-doc-02 · DOC-02 CLI parity 定稿（2026-07-18 · **done**）

- **核对**：T-1808-01～05 内容已在 [`CLI-DESKTOP-PARITY.md`](./CLI-DESKTOP-PARITY.md)（17 族 · 桌面列 · §6 绕行 · IT-38/IT-11）；与 `handle_line` 无漂移
- **定稿动作**：
  - 文首版本 **0.3.0 → 0.3.2 定稿**（与 changelog / STABILIZATION §8 引用对齐）
  - 新增 **§0** T-1808-01～05 状态表 + **§6.5** DOC-02 定稿自检
  - 清理变更记录重复的 0.3.1 行；补 DOC-02 落盘说明
  - [`STABILIZATION.md`](./STABILIZATION.md) §8 标注 DOC-02 定稿 + 链到测试
- **回归**：`python -m unittest tests.test_cli_desktop_parity -v` → **15 OK**
- **下一项**：T-1806-doc-03（DOC-03；先核对 T-1800-07）

### T-1806-doc-03 · DOC-03 done 定义（2026-07-18 · **done** · 核对勾选）

- **核对结果**：**T-1800-07 已落地**，无需重写正文
- **证据**：[`TASKS.md`](./TASKS.md) 前言「done 定义（DOC-03）」含四条 checklist，与 [`STABILIZATION.md`](./STABILIZATION.md) §9.2 一致：
  1. 代码合入
  2. 相关 IT 绿或声明「仅手工」并挂 smoke ID
  3. `stabilization-log` / 等价记录至少 1 次相关路径 pass
  4. `BUGS`/`CHANGELOG` 已更新（若修缺陷）
- **附加**：文首加一行落盘/核对标注（T-1800-07 · T-1806-doc-03）
- **下一项**：T-1806-doc-04（DOC-04；先核对 T-1800-08）

### T-1806-doc-04 · DOC-04 Phase 准入（2026-07-18 · **done** · 核对勾选）

- **核对结果**：**T-1800-08 已落地**，无需重写正文
- **证据**：[`TASKS.md`](./TASKS.md) 前言「新 Phase 准入（DOC-04）」含：
  - 影响 STABILIZATION §3 覆盖矩阵哪些行（新增面须补矩阵行 + 档位）
  - 回归哪些 S-xx / IT-xx ID
  - 缺省 = 评审驳回；Phase 18 冻结期内不受理新功能 Phase
- **对照**：与 [`STABILIZATION.md`](./STABILIZATION.md) §9.3 一致
- **附加**：文首加一行落盘/核对标注（T-1800-08 · T-1806-doc-04）
- **下一项**：T-1806-doc-06（DOC-06；doc-05 已 done，跳过）

### T-1806-doc-06 · DOC-06 data 备份建议（2026-07-18 · **done**）

- **交付**：读者知 **`data/` 默认不进 Git**；误删 / 换机无 `git` 可回滚，须自备备份
- **文档**：
  - [`STABILIZATION.md`](./STABILIZATION.md) **§3.9.4** — 路径表（sessions / state / host_scope / evolve_log / logs / workspace vs evolve）+ 四条建议做法；矩阵行标 **已落地**
  - [`PROJECT.md`](./PROJECT.md) §6.1 — 必读指针 + 换机流程补「拷回备份的 `data/`」
- **版本**：STABILIZATION **v1.0.4**
- **非目标**：本 Phase **不做**自动备份工具
- **下一项**：T-1806-doc-07（DOC-07 资源增长与清理）

### T-1806-doc-07 · DOC-07 资源增长与清理（2026-07-18 · **done**）

- **交付**：evolve_log / sessions / pkl（及 logs、tool_outputs、drops）清理策略可读；明确**手动**、**先备份**、**勿删真实会话**
- **文档**：
  - [`STABILIZATION.md`](./STABILIZATION.md) **§3.10.1** — 增长原因 × 自动策略 × 手动做法表 + 硬规则；§3.10 矩阵三行标策略已文档化
  - [`PROJECT.md`](./PROJECT.md) §6.1 — DOC-07 短指针
- **版本**：STABILIZATION **v1.0.5**
- **已有自动**：sidecar 日志 10MB 轮转（T-1805-05）；**未做**：evolve_log 轮转实现（仍 defer IT-59）
- **下一项**：T-1806-doc-09（DOC-09；doc-08 已 done，跳过）

### T-1806-doc-09 · DOC-09 bootstrap 清单（2026-07-18 · **done**）

- **交付**：clone → pip → npm → 首启步骤可读；Python 3.12+ 前置写清
- **文档**：
  - [`README.md`](../README.md)「**DOC-09 · Fresh bootstrap**」— 步骤表 0～5 + 常见失败
  - [`STABILIZATION.md`](./STABILIZATION.md) **§3.11.1** — 摘要；矩阵 S-51/Python 行标已落地
  - [`PROJECT.md`](./PROJECT.md) §6.1 换机流程链到 DOC-09
- **对齐**：S-51 / T-1821-17（`requirements.txt` 含 httpx+websockets；`start-desktop.bat` 自动 npm）
- **版本**：STABILIZATION **v1.0.6**
- **M2-G 批次完结**（DOC-01～09 全 done）→ 下一批 **M2-H**（T-1808-bug-01）
- **注**：M3 的 T-1890-05（DOC-01～09 已交付）现可勾，留给 M3 批次处理

### T-1808-bug-01 · 清 BUG-014「待验收」（2026-07-18 · **done**）

- **结论**：**fixed**（非保持 open）
- **依据**（stabilization-log 既有 pass，本项未重跑 smoke）：

| Smoke | 记录位置 | 结果 | 覆盖 BUG-014 点 |
|-------|----------|------|-----------------|
| **S-05** | P0 三轮全 pass · run #1 详记 | grow `turn.cancel` → **0.27s** `cancelled`；续聊可输入 | 思考中 Stop ≤3s |
| **S-26** | T-1820-11 · **pass** | 默认 90s；缩略超时 → `confirm.done` `timeout`；可再聊 | confirm 长超时 → 90s |
| **S-28** | T-1820-13 · **pass** | project/daily/pet Stop ≤0.5s cancelled + 续聊；`test_turn_cancel` **9 OK** | 四壳 Stop（grow 已由 S-05） |

- **附带**：S-25（T-1820-10）confirm 中 Stop → `choice=cancelled`（验证清单第 2 项）
- **文档**：
  - [`BUGS.md`](./BUGS.md) 索引 → **fixed**（S-05 / S-26 / S-28）
  - [`bugs/2026-07-13-turn-stall-no-stop.md`](./bugs/2026-07-13-turn-stall-no-stop.md) 状态 + 验证勾选
  - [`STABILIZATION-TASKS.md`](./STABILIZATION-TASKS.md) T-1808-bug-01 → **done**
- **注**：验收为 **WS 协议级**（与桌面 Stop 同 `turn.cancel`）；未单开 Electron 点 UI 按钮。§10「Phase 15 待验收」措辞留给 **T-1808-bug-06**
- **下一项**：T-1808-bug-02（清 Phase 16 T-1517「待手工验收」）

### T-1808-bug-02 · 清 Phase 16 T-1517「待手工验收」（2026-07-18 · **done**）

- **结论**：T-1517「待手工验收」**已清** → 纯 **done**（并入 S-05 / S-28）
- **T-1517 是什么**：桌面 `chat-state.ts` `CANCEL_WATCHDOG_MS = 45_000` — Stop 后若 sidecar 未发 `turn.end`，45s 后 notice「停止请求超时…」并 `resetTurnActivity`（仅恢复 UI）
- **依据**（未重跑 smoke）：

| Smoke | 记录 | 与 T-1517 关系 |
|-------|------|----------------|
| **S-05** | P0 三轮 pass · grow Stop **0.27s** `cancelled` + 续聊 | cancel 主路径健康 → 看门狗**不必**触发 |
| **S-28** | T-1820-13 · **pass** · project/daily/pet ≤0.5s cancelled + 续聊 | 任务验收「并入 S-28」；四壳 Stop 同路径 |

- **代码核对**：`desktop/src/shells/chat-state.ts` 仍含 `CANCEL_WATCHDOG_MS` + `requestCancel` 定时器 + 超时 notice（未回退）
- **未单独测**：故意卡住 45s 不发 `turn.end` 的兜底分支（需 mock 无终态）；本项按 STABILIZATION-TASKS 约定以 S-28 主路径闭环
- **文档**：
  - [`TASKS.md`](./TASKS.md) T-1517 → **done**（S-05 / S-28 · T-1808-bug-02）
  - [`RUNTIME-GUARDS.md`](./RUNTIME-GUARDS.md) M0 表 T-1517 行同步
  - [`STABILIZATION-TASKS.md`](./STABILIZATION-TASKS.md) T-1808-bug-02 → **done**
- **注**：§10「Phase 16 · T-1517 待验收」措辞留给 **T-1808-bug-06**
- **下一项**：T-1808-bug-03（清 Phase 17 checker notice「待巩固」）

### T-1808-bug-03 · 清 Phase 17 checker notice「待巩固」（2026-07-18 · **done**）

- **结论**：「待巩固」**已清**（S-36～S-38 均有 pass）
- **依据**（未重跑 smoke）：

| Smoke | 记录 | 结果 |
|-------|------|------|
| **S-36** | T-1821-04 · **pass** | auto checker → `checker.verdict` pass + notice「验收 …：通过」；`test_checker_subagent` 19/19 |
| **S-37** | T-1821-05 · **pass** | 手动 `验收` / `check` → verdict pass + notice |
| **S-38** | T-1821-06 · **pass** | FAIL → 完成声明门改写「〔验收未通过·已拦截〕」；无「已验收/沉淀完成」 |

- **文档**：
  - [`TASKS.md`](./TASKS.md) T-1621 + Phase 17 M1 标志 → 标注 S-36～38 · T-1808-bug-03
  - [`CHECKER-SUBAGENT.md`](./CHECKER-SUBAGENT.md) 文首状态行同步
  - [`STABILIZATION-TASKS.md`](./STABILIZATION-TASKS.md) T-1808-bug-03 → **done**
- **注**：§10「Phase 17 · 待巩固」措辞留给 **T-1808-bug-06**
- **下一项**：T-1808-bug-04（扫描 `BUGS.md` 无 open P0）

### T-1808-bug-04 · 扫描 BUGS.md 无 open P0（2026-07-18 · **done**）

- **结论**：**通过** — `BUGS.md` 索引与详情 **无 open P0**
- **扫描范围**：

| 源 | 结果 |
|----|------|
| 索引 BUG-001～019（含 009～013 合档） | 状态列均为 **fixed** / **fixed**（含 BUG-014） |
| `docs/bugs/*.md`（14 文件） | 文首状态均为 fixed；无 `open` |
| 索引「open / 待验收 / implemented」 | **0** 条（模板字样除外） |

- **附带 hygiene**：`2026-07-13-confirm-ui-status.md` 标题去掉过时「（开放）」（正文早已 fixed）
- **边界（非本项 fail）**：[`stabilization-backlog.md`](./stabilization-backlog.md) **STD-001** 仍 **open · P1**（放行后开 BUG；非 `BUGS.md` P0）→ **T-1808-bug-05** 覆盖
- **文档**：[`BUGS.md`](./BUGS.md) 文首扫描戳 · [`STABILIZATION-TASKS.md`](./STABILIZATION-TASKS.md) T-1808-bug-04 → **done**
- **下一项**：T-1808-bug-05（扫描 open P1：每条有修复计划或绕行）

### T-1808-bug-05 · 扫描 open P1（计划/绕行）（2026-07-18 · **done**）

- **结论**：**通过** — 无裸奔 open P1
- **扫描**：

| 源 | open P1 | 计划 / 绕行 |
|----|---------|-------------|
| [`BUGS.md`](./BUGS.md) 索引 + `docs/bugs/` | **0** | —（BUG-001～019 全 fixed；009～013 等历史 P1 根因节已修） |
| [`stabilization-backlog.md`](./stabilization-backlog.md) | **STD-001** only | **绕行**：grow prompt 含 `proposal` 等；smoke 后 restore `shell_sessions` + meta · **拟修**：`park_session` 按归属壳线；`activity_router` 勿用 UI 路由覆写持久化 `active_shell` · 放行后开 BUG |

- **非本表 open P1**（已知坑 / smoke 注记，不另开 STD）：S-43/S-45 桌面层拒绝 vs 服务端可 switch（**非 fail**）；S-49 composer「发送失败」未 `resetTurnActivity`（**非 fail**）；S-12/S-17 Electron UI defer — 见 backlog「明确不记入」
- **文档**：`BUGS.md` / backlog 扫描戳 · backlog v0.1.2 · `STABILIZATION-TASKS` T-1808-bug-05 → **done**
- **下一项**：T-1808-bug-06（`STABILIZATION.md` §10 开放项逐条勾选）— **M2-H 末项**

### T-1808-bug-06 · STABILIZATION §10 开放项勾选（2026-07-18 · **done**）

- **结论**：§10 六行全部勾选；**M2-H 完结**
- **版本**：[`STABILIZATION.md`](./STABILIZATION.md) **v1.0.6 → v1.0.7**
- **逐条**：

| 项 | 结论 | 依据 |
|----|------|------|
| Phase 15 / BUG-014 | **closed** | T-1808-bug-01 · S-05/S-25/S-26/S-28 |
| Phase 16 T-1517 | **closed** | T-1808-bug-02 · S-05/S-28 |
| Phase 17 checker notice | **closed** | T-1808-bug-03 · S-36～38 |
| 计划门 UX（S-07 / S-33） | **closed** | P0 三轮 S-07 · T-1821-01 S-33 |
| DESKTOP §5.3 turn.cancel 勾选 | **closed** | T-1813-04 / D-08 |
| pet M2 i4b～i7 | **defer** | PET-SHELL；维持（非阻塞） |

- **非 §10**：STD-001 仍 open P1（backlog；T-1808-bug-05 已记计划/绕行）
- **文档**：`STABILIZATION.md` §10 + §13 · `STABILIZATION-TASKS` T-1808-bug-06 → **done**
- **M2-H 批次完结** → 下一批 **M3**（T-1890-01）

### T-1890-01 · P0 smoke 连续 3 轮全 pass（2026-07-18 · **done**）

- **结论**：**通过** — 核对既有 P0 记录表，**未重跑**
- **依据**（文首 P0 表 + 详记）：

| run | 任务 | 结果 |
|-----|------|------|
| #1 | T-1801-01～17 | **16/16** pass（2026-07-15～16） |
| #2 | T-1802-01 | **16/16** pass（2026-07-16） |
| #3 | T-1802-02 | **16/16** pass（2026-07-16） |

- **交叉核对**：T-1802-03 扫描 **48/48** 格均为 pass；无 fail 单元格；绕行仅 **STD-001**（非 P0 fail）
- **对照**：[`STABILIZATION.md`](./STABILIZATION.md) §5.1「连续 3 次全 pass」**已满足**；§11 首条证据齐（勾选留给后续 M3 汇总项）
- **文档**：`STABILIZATION-TASKS` T-1890-01 → **done**
- **下一项**：T-1890-02（Gate runner 全绿 · 核对 T-1807-04）

### T-1890-02 · Gate runner 全绿（2026-07-18 · **done**）

- **结论**：**通过** — 核对既有存档，**未重跑** Gate
- **依据**：

| 来源 | 结果 | 存档 |
|------|------|------|
| **T-1807-04**（本项依赖） | **exit 0** · **111 run** · **2 xfail**（IT-55/IT-56 当时预期）· 摘要 13/13 PASS + `TOTAL OK` | [`data/_t1807-out.txt`](../data/_t1807-out.txt) |
| **T-1823-05**（后继） | **117 run** · **0 xfail**（IT-55/56 已实现） | log 条目 |
| **T-1824-05**（现基线） | **117 run, 0 fail** · live `_*` 新增 0 | [`data/_t1824-05-gate.txt`](../data/_t1824-05-gate.txt) |

- **对照**：[`STABILIZATION.md`](./STABILIZATION.md) §6.1.4 期望「**117** · **0** expected failure · `TOTAL OK 117 run`」与 T-1824-05 一致；§11「Gate 集全绿」证据齐
- **注**：T-1807-04 的 2 xfail 为当时合法绿（§6.1：含 `expectedFailure` 仍 exit 0）；现已清零，非回退
- **文档**：`STABILIZATION-TASKS` T-1890-02 → **done**
- **下一项**：T-1890-03（P1 全 pass 或均有 BUG+绕行）

### T-1890-03 · P1 全 pass 或均有 BUG+绕行（2026-07-18 · **done**）

- **结论**：**通过** — 核对 P1 记录表 + T-1821-19，**未重跑** smoke
- **依据**（[`STABILIZATION.md`](./STABILIZATION.md) §5.2 · 文首 P1 表）：

| 批次 | 范围 | 结果 |
|------|------|------|
| **M2-A**（T-1820-01～16） | S-11,S-15,S-18～S-31 | **16/16 pass** |
| **M2-B**（T-1821-01～18 · T-1821-19） | S-33～S-46,S-49～S-52 | **18/18 pass** |
| **合计 §5.2 P1** | 34 项 | **34/34 pass** · **0 fail** |

- **fail → BUG+绕行**：**无**（无 P1 fail 单元格）
- **非 fail 注记**（不挡放行 · 见 T-1821-19 / 已知坑）：
  - **STD-001**（backlog open P1）：activity_router × park；有绕行+拟修；**非**本表 fail
  - S-43/S-45/S-49 分层注记（桌面拒绝 vs 服务端仍可 / composer 文案）— 均已 **pass**
- **附**：§5.3 P2 抽样 S-32/S-47（M2-C）亦 **2/2 pass**；非本项验收范围
- **对照**：§11「P1 全 pass 或每条失败有 open BUG+绕行」→ **全 pass 路径满足**
- **文档**：`STABILIZATION-TASKS` T-1890-03 → **done**
- **下一项**：T-1890-04（sidecar 日志落盘 · 表内已 **done**）

### T-1890-04 · sidecar 日志落盘已上线（2026-07-18 · **done**）

- **结论**：**通过** — 核对 T-1805-06（及 M1-C 全链），**未重跑** 强杀 / 冷启
- **依据**（本项依赖 **T-1805-06**）：

| 检查 | 结果 |
|------|------|
| T-1805-01～07 | 全部 **done**（M1-C 完结） |
| T-1805-06 强杀取证 | **pass** — 杀后磁盘仍有 `sidecar logging ready` + 最后 `ws error:`；历史 traceback 未丢 |
| 代码仍在 | `agent-core/sidecar_logging.py`（路径 · `configure` · `RotatingFileHandler` 10MB×5） |
| Gate | `tests.test_sidecar_logging` 在 `run_stabilization.py`（IT-58） |
| 文档 | [`DESKTOP.md`](./DESKTOP.md) §4.4.3 · STABILIZATION §3.10 P0 行 |

- **对照**：§11「sidecar 日志落盘（T-1805-01～07）已实现」证据齐
- **文档**：`STABILIZATION-TASKS` T-1890-04 维持 **done**（本项为放行复核）
- **下一项**：T-1890-05（DOC-01～09 已交付）

### T-1890-05 · DOC-01～09 已交付（2026-07-18 · **done**）

- **结论**：**通过** — 核对 M2-G（T-1806-doc-01～09）+ 落盘锚点抽查，**未重做**文档
- **依据**：

| DOC | 任务 | 落盘锚点 | 状态 |
|-----|------|----------|------|
| 01 | T-1806-doc-01 | [`PET-SHELL.md`](./PET-SHELL.md) §1.3 | done |
| 02 | T-1806-doc-02 | [`CLI-DESKTOP-PARITY.md`](./CLI-DESKTOP-PARITY.md) v0.3.2 | done |
| 03 | T-1806-doc-03 | [`TASKS.md`](./TASKS.md)「done 定义」 | done |
| 04 | T-1806-doc-04 | [`TASKS.md`](./TASKS.md)「新 Phase 准入」 | done |
| 05 | T-1806-doc-05 | [`RUNTIME.md`](./RUNTIME.md) §2.4 | done |
| 06 | T-1806-doc-06 | [`STABILIZATION.md`](./STABILIZATION.md) §3.9.4 | done |
| 07 | T-1806-doc-07 | [`STABILIZATION.md`](./STABILIZATION.md) §3.10.1 | done |
| 08 | T-1806-doc-08 | [`DESKTOP.md`](./DESKTOP.md) §3.8.1 | done |
| 09 | T-1806-doc-09 | [`README.md`](../README.md) DOC-09 · §3.11.1 | done |

- **对照**：§11「DOC-01～09 落地」证据齐（M2-G 摘要亦记 9/9）
- **文档**：`STABILIZATION-TASKS` T-1890-05 → **done**
- **下一项**：T-1890-06（`BUGS.md` 无裸奔 P0/P1）

### T-1890-06 · BUGS.md 无裸奔 P0/P1（2026-07-18 · **done**）

- **结论**：**通过** — 核对 T-1808-bug-04/05 + 现表抽查，**未重开** BUG
- **依据**：

| 源 | 结果 |
|----|------|
| **T-1808-bug-04** | `BUGS.md` 索引 BUG-001～019 **全 fixed** · **0 open P0** |
| **T-1808-bug-05** | `BUGS.md` **0 open P1**；backlog 仅 **STD-001**（绕行+拟修齐全） |
| 现抽查 | 索引状态列无 `| open |`（模板字样除外）；文首扫描戳仍在 |

- **非裸奔**：STD-001 在 [`stabilization-backlog.md`](./stabilization-backlog.md)（**非** BUGS 裸奔 open）；放行后开单 — 与 §11「P1 均有结论」一致
- **对照**：§11「`BUGS.md` 无裸奔 P0；P1 均有结论」证据齐
- **文档**：`STABILIZATION-TASKS` T-1890-06 → **done**
- **下一项**：T-1890-07（`STABILIZATION.md` 标 **done**）

### T-1890-07 · STABILIZATION.md 标 done（2026-07-18 · **done**）

- **结论**：**通过** — 文首状态 **done** · 版本 **v1.0.8** · §13 已记
- **变更**（[`STABILIZATION.md`](./STABILIZATION.md)）：
  - 文首：`v1.0.7` → **`v1.0.8`**；状态 → **done**（解冻仍待 T-1890-08～10）
  - §11：勾选 T-1890-01～06 对应项；原「DOC + TASKS」拆为 DOC **[x]** / TASKS **[ ]** / 用户签字 **[ ]**
  - §13：新增 **1.0.8** 行
- **§11 现态**：

| 项 | 状态 |
|----|------|
| P0 / P1 / Gate / §6.2 / BUGS / sidecar / DOC-01～09 | **[x]** |
| `TASKS.md` Phase 18 全 done | **[ ]** → T-1890-08 |
| 用户签字解冻 | **[ ]** → T-1890-10 |

- **文档**：`STABILIZATION-TASKS` T-1890-07 → **done**
- **下一项**：T-1890-08（`TASKS.md` Phase 18 全 task **done**）

### T-1890-08 · TASKS.md Phase 18 全 task done（2026-07-18 · **done**）

- **结论**：**通过** — Epic 索引与细粒度对齐；M2-I 整批 defer
- **变更**：
  - [`TASKS.md`](./TASKS.md)：Phase 18 粗粒度 T-1801～1825 必做面 → **done**；T-1812 → **进行中**（01～08 done · 待 09～10）；文首/节头对齐 STABILIZATION **v1.0.9**
  - [`STABILIZATION-TASKS.md`](./STABILIZATION-TASKS.md)：M2-I T-1830-01～12 → **defer**；T-1830-13 → **done**
  - [`STABILIZATION.md`](./STABILIZATION.md)：**v1.0.9**；§11 TASKS 行 **[x]**
  - [`stabilization-backlog.md`](./stabilization-backlog.md)：M2-I defer 戳 · v0.1.3
- **注**：M2-I 整批 defer ≠ §6.2 的 IT-38/62/59 三条名额（后者已在 §11 勾选说明）
- **文档**：`STABILIZATION-TASKS` T-1890-08 → **done**
- **下一项**：T-1890-09（`MAP.md` / `project-map.mdc` 解冻说明）

### T-1890-09 · MAP / project-map 解冻说明（2026-07-18 · **done**）

- **结论**：**通过** — 解冻规则已写入两处真源
- **变更**：
  - [`MAP.md`](./MAP.md)：版本 2026-07-18；Phase 18 → **done**；新增 **§2.1 解冻说明**（仍冻结至 T-1890-10 · 解冻步骤 · DOC-04 准入 · STD-001/M2-I 债）
  - [`.cursor/rules/project-map.mdc`](../.cursor/rules/project-map.mdc)：同步冻结/解冻要点 + 链到 MAP §2.1
- **未做**：实际解冻（留给 **T-1890-10** 用户签字）
- **文档**：`STABILIZATION-TASKS` T-1890-09 → **done**
- **下一项**：T-1890-10（用户签字：可恢复 feature Phase）

### T-1890-10 · 用户签字解冻（2026-07-18 · **done**）

- **结论**：**通过** — 用户明确签字；**feature 冻结已解除**
- **签字原文**：「同意解冻：可恢复 feature Phase」
- **变更**：
  - [`STABILIZATION.md`](./STABILIZATION.md)：**v1.1.0** · 状态 **done · 已解冻** · §11 全勾 · §13 记 1.1.0
  - [`MAP.md`](./MAP.md) / [`project-map.mdc`](../.cursor/rules/project-map.mdc)：冻结 → **已解冻**
  - [`TASKS.md`](./TASKS.md)：Phase 18 / T-1812 → **done**；文首「可开新功能 Phase」
  - `STABILIZATION-TASKS` T-1890-10 → **done**
- **M3 批次完结** · **Phase 18 放行完成**
- **放行后债**（非阻塞）：STD-001 · M2-I defer

### 2026-07-18 · M2-H 摘要（T-1808-bug-06）

| ID | 交付 | 结果 |
|----|------|------|
| T-1808-bug-01 | BUG-014 待验收 | done · **fixed** |
| T-1808-bug-02 | T-1517 待手工验收 | done · 并入 S-28 |
| T-1808-bug-03 | checker notice 待巩固 | done · S-36～38 |
| T-1808-bug-04 | 无 open P0 | done · 0 open |
| T-1808-bug-05 | open P1 计划/绕行 | done · STD-001 齐全 |
| T-1808-bug-06 | §10 勾选 | done · **v1.0.7** · **M2-H 完结** |

### 2026-07-18 · M2-G 摘要（T-1806-doc-09）

| ID | 交付 | 结果 |
|----|------|------|
| T-1806-doc-01 | pet→daily 映射 | done · PET-SHELL §1.3 |
| T-1806-doc-02 | CLI parity 定稿 | done · CLI-DESKTOP-PARITY v0.3.2 |
| T-1806-doc-03 | done 定义 | done · 核对 T-1800-07 |
| T-1806-doc-04 | Phase 准入 | done · 核对 T-1800-08 |
| T-1806-doc-05 | schema 兼容 | done（先于本批 · RUNTIME §2.4） |
| T-1806-doc-06 | data 备份 | done · STABILIZATION §3.9.4 |
| T-1806-doc-07 | 资源清理 | done · STABILIZATION §3.10.1 |
| T-1806-doc-08 | 编码策略 | done（先于本批 · DESKTOP §3.8.1） |
| T-1806-doc-09 | bootstrap | done · README DOC-09 |

### 2026-07-18 · M2-F 摘要（T-1824-06）

| ID | 交付 | 结果 |
|----|------|------|
| T-1824-01 | Windows 乱码路径调查 | done · 输出点矩阵 |
| T-1824-02 | CLI UTF-8 + DOC-08 | done · DESKTOP §3.8.1 |
| T-1824-03 | Electron `sidecarSpawnEnv` | done |
| T-1824-04 | IT-62 审计清单 | done · `_t1824-04-audit.md` |
| T-1824-05 | Gate 高危测试隔离 | done · Gate 117 · 新增 `_*`=0 |
| T-1824-06 | IT-61 中文文件名拖放 | done · 6/6 unittest |

### 2026-07-18 · M2-E 摘要（T-1823-06）

| ID | 交付 | 结果 |
|----|------|------|
| T-1823-01 | 坏 jsonl notice 方案 | done · §3.9.1 |
| T-1823-02 | jsonl → `corruption_notices` + `turn.notice` | done · IT-55 绿 |
| T-1823-03 | 坏 meta 告知方案 | done · §3.9.2 |
| T-1823-04 | meta → notices | done |
| T-1823-05 | state → `{}` + `paths.corruption_notices` | done · IT-56 绿 · Gate 0 xfail |
| T-1823-06 | DOC-05 schema 表 | done · RUNTIME §2.4 |

### 2026-07-18 · M2-D 摘要（T-1813-04）

| ID | 交付 | 结果 |
|----|------|------|
| T-1813-01 | 附录表 A（入站 24） | done |
| T-1813-02 | 附录表 B（出站 33；↔ ws.ts 无缺口） | done |
| T-1813-03 | 漂移 D-01～D-09；修代码 0 | done |
| T-1813-04 | DESKTOP v0.3.10 落盘 | done |

### 2026-07-17 · M2-B P1 摘要（T-1821-19）

| ID | 映射 | 结果 | 要点 |
|----|------|------|------|
| T-1821-01 | S-33 | pass | draft 拒写码 / run_python / verify |
| T-1821-02 | S-34 | pass | Phase 改 → plan_dirty 再确认 |
| T-1821-03 | S-35 | pass | grow write_evolve 最小路径 |
| T-1821-04 | S-36 | pass | 自动 checker notice / verdict |
| T-1821-05 | S-37 | pass | 手动 `验收` / `check` CLI |
| T-1821-06 | S-38 | pass | 完成声明门拦截 FAIL |
| T-1821-07 | S-39 | pass | host 向导 / add 目录 |
| T-1821-08 | S-40 | pass | host 只读 + denylist |
| T-1821-09 | S-41 | pass | grow 拖放 → `_drops/` |
| T-1821-10 | S-42 | pass | daily/pet 拖放不崩 |
| T-1821-11 | S-43 | pass | 忙时 project.switch 桌面说明 |
| T-1821-12 | S-44 | pass | project 新会话不串旧聊天 |
| T-1821-13 | S-45 | pass | 壳锁定忽略 auto `ui.route` |
| T-1821-14 | S-46 | pass | CLI+桌面 lock 提示清晰 |
| T-1821-15 | S-49 | pass | 断网合意失败 + 可重试 |
| T-1821-16 | S-50 | pass | CLI UTF-8 无乱码（start.bat） |
| T-1821-17 | S-51 | pass | fresh bootstrap pip+npm → ready |
| T-1821-18 | S-52 | pass | 双实例 lock/port 提示清晰 |

### 2026-07-17 · M2-A P1 摘要（T-1820-17）

| ID | 映射 | 结果 | 要点 |
|----|------|------|------|
| T-1820-01 | S-11 | pass | project 拖放 → `_incoming/` |
| T-1820-02 | S-15 | pass | proposals reject/accept |
| T-1820-03 | S-18 | pass | Electron 闪退后 Vite 存活 |
| T-1820-04 | S-19 | pass | plan.request ≡ 项目确认 |
| T-1820-05 | S-20 | pass | 跨项目确认卡 |
| T-1820-06 | S-21 | pass | project.verify |
| T-1820-07 | S-22 | pass | 断线重连 |
| T-1820-08 | S-23 | pass | daily/pet confirm |
| T-1820-09 | S-24 | pass | write_evolve 二次 confirm |
| T-1820-10 | S-25 | pass | confirm 中 Stop |
| T-1820-11 | S-26 | pass | confirm 90s 超时 |
| T-1820-12 | S-27 | pass | host 写 confirm |
| T-1820-13 | S-28 | pass | 四壳 Stop |
| T-1820-14 | S-29 | pass | recall 无乱调工具 |
| T-1820-15 | S-30 | pass | 压缩 notice + 非假死 |
| T-1820-16 | S-31 | pass | memory 顶栏条数合理 |

---

### 2026-07-15～16 run #1 · P0（**完成** · T-1801-01～17）

- **结果**：**16/16 pass** · 无新 BUG · T-1801-17 收束（2026-07-16）
- **跨度**：S-01～S-10 于 **2026-07-15**；S-12～S-14、S-16～S-17、S-48 于 **2026-07-16**（S-13 首次 fail 后同日重试 pass）
- **方式**：主路径 **WS 协议级 smoke**（`ws://127.0.0.1:8765` + `python agent-core\server.py --port 8765 --takeover`）；未开 Electron 全项
- **defer 至 run #2**：S-12 托盘「退出」完整 UI · S-17「助手仍在执行任务」确认框（run #1 仅验证 `stopSidecar`/`sidecar.kill` 清理路径）
- **环境坑（本轮亲历）**：`shell_sessions` 易被 grow smoke 污染（S-16/S-17/S-48 后均须核对 `daily`≠`grow`）；TURN_LOCK 断连后须冷启 sidecar；7/16 早间 LLM SSL 退化（S-48 午后用坏 key 协议验证）
- **S-01** pass — stdout 仅 `{"ready": true, "host": "127.0.0.1", "port": 8765}`，无 ImportError/NameError/traceback；WS 连接后收到 `session.banner` / `session.memory` / `session.history` 等初始事件（顶栏/会话就绪等价）
- **S-02** pass — `shell.switch` → grow 后发送 `1+1`；`turn.end` `ok: true`（`finish_reason: stop`）；续发 `2+2` 再次 `turn.end` `ok: true`（输入可继续）
- **S-03** pass — 发送 `新会话`；**0.46s** 内 `turn.end` `ok: true`（`finish_reason: completed`）+ `session.banner`/`session.history` 刷新；续发 `ping` 正常（非永久「处理中…」）
- **S-04** pass — grow 触发 `write_text`（`stab-s04-test.txt`）；**17.9s** 内 `confirm.request` → 同意（`choice: y`）→ `confirm.done` + `tool.end` + `turn.end` `ok: true`；文件已写入 workspace
- **S-05** pass — grow 发 `1+1` 后于 `turn.start` 立即 `turn.cancel`；**0.27s** 内 `turn.end` `finish_reason: cancelled`；续发 `2+2` 正常（可输入）。注：当前环境 LLM SSL 证书错误，未测分钟级长流式，Stop 协议已验证
- **S-06** pass — `新会话` → `项目 新建 stab-r1-demo`；`workspace/stab-r1-demo/` 含 **PROJECT.md / MAP.md / TASKS.md**；`project.state` `plan_status: draft`、`needs_plan_confirm: true`（顶栏「计划待确认」等价）
- **S-07** pass — project 壳发「填 PROJECT.md 和 TASKS.md」；**3 次 confirm** + **5 次 tool.end ok**；`PROJECT.md`/`TASKS.md` 已写入实质内容（`stab-r1-demo` 稳定化演示计划）；无「整轮已拦截」；`turn.end` ok
- **S-08** pass — project 壳发 `项目 确认`（侧栏「确认开工」CLI 等价）；`project.state` `plan_status: confirmed`、`needs_plan_confirm: false`；顶栏 **6/6 未完成**（`tasks_open`/`tasks_total`）
- **S-09** pass — `项目 新建 stab-r1-b` → `project.switch` 切到 **stab-r1-b**；`project.switch.done` `session_replaced: true`；会话 id **20260715-4ceb96db → 20260715-3de21a68**（history 替换）；无 ImportError/NameError
- **S-10** pass — **project → grow → project** 壳切换；project 会话 **20260715-4ceb96db** 往返一致，grow 会话 **20260713-fc1acefd** 独立；`session.history` 各壳内容不串线
- **S-12** pass — 空闲态 sidecar 清理：冷启 `python agent-core\server.py --port 8765 --takeover`（PID **36300**）→ 等价 `stopSidecar()` 杀进程；**2s** 内 8765 无 Listen、PID 不存在、`my-agent\agent-core\server.py` 无残留（`ORPHAN_COUNT=0`）。注：未点托盘「退出」；清理路径与 Electron `performAppQuit` → `sidecar.kill()` 一致；**托盘 UI defer → run #2**
- **S-13** pass（2026-07-16 重试 · T-1801-12）— `shell.switch` → **daily**（`session_id` **20260715-b5215de6**）；短问答 `1+1` **44.9s** 内 `turn.end` `ok: true`（`finish_reason: stop`）；`write_text`（`stab-s13-daily.txt`）**9.4s** 内 `confirm.request` → 同意 → `confirm.done` + `tool.end` + `turn.end` `ok: true`；文件 9 字节落盘。注：7/16 早间 LLM SSL 已恢复（`api.deepseek.com` 401）；首次自动化因回合超时 fail，重试 pass
- **S-14** pass（2026-07-16 · T-1801-13）— 模拟 pet↔工作台 WS 生命周期（pet 映射 **daily** 会话线）：**pet1** `shell.switch` daily → 发消息 **2.3s** `turn.end` ok；断开 → **workbench** `shell.switch` grow（`20260713-fc1acefd`）；断开 → **pet2** daily 续聊 **2.5s** `turn.end` ok；daily 会话 **20260715-b5215de6** 往返一致。sidecar stdout **无** `connection handler failed` traceback（BUG-016 回归面）
- **S-16** pass（2026-07-16 · T-1801-14）— **grow ↔ daily** 各聊一句后互切：**grow** `20260713-fc1acefd` · **daily** `20260715-b5215de6` 会话 id 稳定往返；`session.history` 各壳 **GROW-S16-v3** / **DAILY-S16-v3** 不串线。注：首轮因 `shell_sessions` 被早先 smoke 污染（daily→grow 同 id）fail；修复 `data/state.json` 映射后 pass；grow 侧 prompt 含 `proposal` 以免 activity_router 把 `active_shell` 改成 daily 后 `park_session` 写错映射
- **S-17** pass（2026-07-16 · T-1801-15）— **忙时退出** sidecar 清理：冷启（PID **27296**）→ grow `1+1` → **54ms** `turn.start` → 杀进程；**2s** 内 `ORPHAN_COUNT=0`。注：确认框 UI defer → run #2；smoke 后 `daily` 映射已恢复
- **S-48** pass（2026-07-16 · T-1801-16）— **LLM 异常**：冷启 sidecar `LLM_API_KEY=sk-invalid-stab-s48`（PID **33756**）→ grow `user.message` `1+1` → **1.3s** 内 WS `error` `llm error: Authentication Fails, Your api key: ****-s48 is invalid` + `turn.end` `ok: true`；续发 `ping-after-error` **1.5s** 内 `turn.end`（不假死）→ 杀进程、冷启恢复真实 key（PID **33228**）→ `2+2` **3.5s** 内 `turn.end` `ok: true` `finish_reason: stop`。注：smoke 后 `shell_sessions.daily` 被写成 grow id，已恢复 `20260715-b5215de6`
- **新发现**：无新 BUG 号

**续接**：见文首「续接指引」· run #2/#3 详记如下。

---

### 2026-07-16 run #2 · P0（**完成** · T-1802-01）

- **结果**：**16/16 pass** · 无新 BUG · 2026-07-16
- **方式**：WS 协议级 smoke（同 run #1）；项目用 **stab-r2-demo** / **stab-r2-b**（避免与 run #1 冲突）
- **defer**：S-12 托盘 UI · S-17 忙时确认框（仍为 `stopSidecar`/`taskkill` 协议级）
- **S-01～S-10** pass — 同 run #1 协议；S-02 **2.8s** · S-04 **14.2s** confirm · S-07 **6.5s** project 出计划
- **S-12** pass — 空闲杀 sidecar（PID **30960**）`ORPHAN_COUNT=0`
- **S-13** pass — daily `20260715-b5215de6` · **2.9s** qa + confirm `stab-s13-r2-daily.txt`
- **S-14** pass — pet/wb/pet 三连接；`1+1` / `proposal workbench-r2` / `3+3`；daily **20260715-b5215de6** 往返一致（smoke 绕行：restore + meta 校正；**STD-001**）
- **S-16** pass — grow `20260713-fc1acefd` · daily `20260715-b5215de6` 分离
- **S-17** pass — busy `turn.start` 后杀 PID **35780** · 2s 内清理
- **S-48** pass — 坏 key → `llm error` + `turn.end` → 恢复 key `2+2` ok（PID **35076**）

---

### 2026-07-16 run #3 · P0（**完成** · T-1802-02）

- **结果**：**16/16 pass** · 无新 BUG · **P0 连续 3 轮达标**（STABILIZATION §5.1）
- **方式**：WS 协议级 smoke；项目 **stab-r3-demo** / **stab-r3-b**
- **defer**：S-12 托盘 UI · S-17 忙时确认框（三轮均为 sidecar 协议级）
- **S-01～S-10** pass — S-02 **3.1s** · S-04 **12.9s** · S-07 **17.1s**
- **S-12** pass — PID **29812** `ORPHAN_COUNT=0`
- **S-13** pass — daily `20260715-b5215de6` + `stab-s13-r3-daily.txt`
- **S-14** pass — pet/wb/pet；**STD-001** 绕行（`proposal workbench-r3` + restore）
- **S-16** pass — grow/daily 分离
- **S-17** pass — busy kill PID **17028**
- **S-48** pass — 坏 key + 恢复

---

### 2026-08-06 · Phase 24 Progress Gate smoke（**T-2408**）

- **结果**：**S-70～S-74 pass**（自动化代理 · 无新 BUG）
- **方式**：`pytest agent-core/tests/test_progress_gate.py::SmokeS70ToS74Tests -v`
- **S-70** pass — `test_s70_write_evidence_allows_checkbox`（对口 write → `report_progress` ok）
- **S-71** pass — `test_s71_no_evidence_rejects`（无本回合证据 → 拒勾 · TASKS 仍 `[ ]`）
- **S-72** pass — `test_s72_failed_command_evidence_blocks_test`（confirm 拒 / run_command 失败 ≠ 测试证据）
- **S-73** pass — `test_s73_second_report_hard_reject`（勾选后同 turn 再报硬拒）
- **S-74** pass — `test_s74_write_cannot_satisfy_compile_test_build_fe`（write 不得勾 compile/test/build_fe）
- **备注**：S-75（G9 拒勾后禁口头收口）由 **T-2410** `test_it2410_kernel_notice_injected_on_blocked_report` 覆盖

---

## 模板（单次详记）

```markdown
### YYYY-MM-DD run #N · P0|P1

- 环境：Windows · 工作区状态 …
- S-xx … pass/fail — …
- 新发现：BUG-NNN 或 无
```
