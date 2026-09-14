# 狂奔模式体验报告与待办

> 版本：0.2.0 · 2026-09-07
> 状态：R7f done · **Phase 59 bug-fix done** · v2 核心测试通过；S-6106 music 真实 0x567 试点已完成
> 关联：[RUNAWAY-FLOW-STATE-MACHINE.md](./RUNAWAY-FLOW-STATE-MACHINE.md) · [RUNAWAY-R6-RELIABILITY.md](./RUNAWAY-R6-RELIABILITY.md) · [BUG-FIX-AGENT.md](./BUG-FIX-AGENT.md) · [TASKS.md](./TASKS.md) UI-6011～6017 · UI-5961～5965

本文记录 `workspace/test`（Music Dreamer）在真实 LLM（**0x567-flash**）下的狂奔体验结论，供后续 Harness / Desktop 收口。手工脚本：`tools/runaway_experience.py`。

## 1. 体验环境

| 项 | 值 |
|---|---|
| 模型 | `0x567-flash`（0x567 Luna） |
| 项目 | `workspace/test` |
| 会话 | `_t5907_6a0f4dcc5d` |
| 脚本 | `tools/runaway_experience.py` |

## 2. 已确认可用（2026-09-01）

### 2.1 核心链路

- **零确认弹窗**：狂奔授权下 `write_text` / `patch_file` / `run_command` 等项目内工具直接执行；`confirm_requests=0`。
- **plan 提案自动采纳**：`plan_partner` 产出在狂奔下自动落盘，不进入计划审阅队列。
- **真实交付**：bootstrap.yml、gateway-service、frontend、database schema、docker-compose、Maven/npm 测试等均有实际文件与命令输出。

### 2.2 Harness 任务切换（R3 补丁 · 2026-09-01 已编码）

**问题（修复前）**：implementation 阶段任务勾选完成后，`active_task` 仍指向旧任务（如 T-002），无法自动切到 T-005，也不会进入 `verifying`。根因是 `_advance_runaway_checkpoint` 依赖 `task_stop_armed`，而狂奔常通过 plan 采纳或跨 turn 勾选 TASKS，不会触发一停门 arming。

**修复**：

- 新增 `_runaway_implementation_progressed()`：检测 `stats.done` 相对 `project_runaway_task_done_baseline` 增量、当前 `active_task` 已勾选、或本回合 `task_stop_armed`。
- implementation 分支不再硬依赖 `task_stop_armed`。
- `begin_turn()` 后、`_continue_runaway_after_natural_stop()` 前、以及 `_prepare_runaway_project_start()` 的 implementation 分支均尝试 advance。
- 切任务 / 进验证时写入 `project_runaway_task_done_baseline = stats.done`（不再重置为 0）。

**验证**：

- `test_advance_runaway_moves_to_next_task_when_active_task_done`
- `test_advance_runaway_enters_verifying_when_open_queue_empty`
- 真实 LLM 第二轮：`T-002 → T-005`（启动前 advance），Turn 2 后 `T-005 → T-006`

## 3. 第二轮真实 LLM 体验（2026-09-01 · ~13 分钟）

| 指标 | 结果 |
|---|---|
| Turn 数 | 4 |
| 总耗时 | ~778s |
| 工具调用 | 25 |
| 确认弹窗 | 0 |
| 最终 checkpoint | `implementing` |
| 最终 active | `T-006` |
| tasks_done | 5 / 27 |

**过程摘要**：

1. 启动前 advance：`T-002` → `T-005`（turn.notice：「当前任务已完成，正在进入下一项…」）。
2. Turn 1：MySQL 连接 exit 1 → `plan_partner` 勾选 T-005 并写 VERIFY V-014。
3. Turn 2：实现 infrastructure 配置 → advance 到 `T-006`。
4. Turn 3～4：Maven 测试、docker-compose 静态检查通过；仍未进入 `verifying`。

## 3b. 第三轮真实 LLM 体验（2026-09-01 · R7 编码后 · ~15 分钟）

| 指标 | 结果 |
|---|---|
| Turn 数 | 4 |
| 总耗时 | **924.9s** |
| 工具调用 | **38** |
| 确认弹窗 | **0** |
| 最终 checkpoint | `implementing` |
| 最终 active | **T-006**（应已 advance 到 T-008） |
| tasks_done | **5 → 7**（T-006、T-007 被 plan 勾选） |
| LLM 504 notice | 每 turn **1 条**（R7-3 生效） |
| segment 摘要 | 有（R7-4 生效） |

**过程摘要**：

1. Turn 1：`plan_partner` 处理 T-006；504 重试 1 次后继续。
2. Turn 2：大量 user-service / JWT 代码写入；plan **越序勾选 T-007**；504 后再续接。
3. Turn 3～4：`patch_file` anchor 重复失败 → 工具失败预算耗尽；仍停在 `implementing`。

**R7 编码后仍暴露的问题**（见 §8）：

- `next_open_task` 曾把 plan 行内 `T-003/T-004` 误判为 **T-003** 开放任务 → **R7-1b 已修**（行首 `T-*`）
- T-006 使用 `（V-015）` 非 `verify:` metadata → advance 未触发 → **R7-2b 已修**
- 修完后会话仍 `active=T-006`，**未再跑** LLM 回归验证 advance

## 3c. 第四轮真实 LLM 体验（2026-09-01 · R7b 后 · ~48 分钟 · Turn 1/4）

| 指标 | 结果 |
|---|---|
| Turn 数 | **1 / 4**（Turn 1 `timeout` 后脚本退出） |
| 总耗时 | **2905.6s** |
| 工具调用 | **58** |
| 确认弹窗 | **0** |
| 最终 checkpoint | `implementing` |
| 最终 active | **T-008** |
| tasks_done | **7 / 25** |
| LLM 504 notice | Turn 1 前段 **2 次**（16:00 上游故障窗口） |
| 上游 200  streak | **16:26～16:33** 同 Luna 池、**80k～130k input** 仍 200 |

**过程摘要**：

1. Turn 1 开头：`pool exhausted / 504` 各 1 次重试后恢复；Harness `T-007 → T-008` advance 生效。
2. Turn 1 中段：3 次 `plan_partner` 收尾 T-007；大量 user-service / gateway / **admin-service / music-song** 代码（任务漂移）；根目录 `mvn -q clean test` **exit 0**。
3. Turn 1 末段：`LLM 请求超时（120s）`；同一 turn 内 **segment 2～6 空转**（duplicate continue_key bug）；脚本遇 `timeout` break，Turn 2～4 未跑。
4. gateway 已有 JWT 过滤器与测试，但 **T-008 未勾选、VERIFY 无 V-017** → 证据闸门仍卡。

**与 Codex 同 Luna 池的对照（Round 4 结论）**：

- 503/504 主要是 **上游间歇故障 + 故障窗口内重试放大**，不是「上下文太大写不了项目」（130k 仍 200）。
- **40k～60k 早压缩** 不采纳；项目上下文应保留到真正接近容量上限再 digest。
- 可控优化：**503 长退避少重试**、tool loop 轮间冷却、`plan_partner` 限频、修复 segment 空转、体验脚本 timeout 不中断多 turn。

## 4. 观感问题（R7 · 2026-09-01）

优先级按用户感知排序，不是实现难度。

### P0 · 必须先修

| ID | 问题 | 用户感受 | 建议方向 |
|---|---|---|---|
| **R7-1** | TASKS 被 plan 提案污染 | 队列里出现「自动采纳并落盘…」等非 `T-*` 开放项 | **调度层 done**（`next_open_task` 只认行首 `T-*`）；**文件级 plan 行仍残留** → R7-9 |
| **R7-2** | 「勾选先于修复」式 advance | MySQL exit 1 后仍勾选并切任务 | **done**（VERIFY / 本回合工具证据）；`（V-NNN）` inline → R7-2b done |

### P1 · 显著降噪

| ID | 问题 | 用户感受 | 建议方向 |
|---|---|---|---|
| **R7-3** | API 504 / pool exhausted 刷屏 | 每个 turn 一条 retry notice | **done**（每 turn 1 条） |
| **R7-4** | 空 assistant 回复 | 多轮结束无自然语言总结 | **done**（Harness segment 摘要） |
| **R7-5** | 「进入下一项」通知抢跑 | plan 刚勾选就提示 advance | **done**（绑定具体任务名 + 实际切换时） |

### P2 · 里程碑感

| ID | 问题 | 用户感受 | 建议方向 |
|---|---|---|---|
| **R7-6** | 长跑后仍停在 implementing | 看不到「任务已清空 · 待验证」 | **逻辑 done**；25 项队列未清空故未触发；Desktop 文案 → UI-6010 |
| **R7-7** | Harness 续接消息堆叠 | 续接 user 消息刷屏 | **done**（同 checkpoint 去重） |

## 8. 仍阻塞 S-6011（R7b · 2026-09-01）

| ID | 状态 | 问题 | 影响 | 编码方向 |
|---|---|---|---|---|
| **R7-1b** | **done** | plan 行内提及 `T-003/T-004` 被当成开放任务 | `next_open_task` 指向污染行；segment 摘要「下一项 T-003」 | 正式任务 ID 必须在**行首** `T-*` |
| **R7-2b** | **done** | `（V-015）` 未进 evidence contract | T-006 勾选后仍不 advance | inline `V-*` + VERIFY 按 task 绑定 |
| **R7-8** | **done** | plan **越序勾选**（active=T-006 时勾 T-007） | 队列与实现脱节 | `revert_out_of_order_runaway_checkoffs` · UI-6018 |
| **R7-9** | **done** | TASKS **文件**仍留 plan 开放行 | 人/LLM 误读队列 | `strip_nonformal_open_task_lines` · UI-6019 |
| **R7-10** | **done** | plan 采纳后未立即 advance | 会话 stale | `_reconcile_runaway_tasks_after_plan` · UI-6020 |
| **R7-11** | **done** | `patch_file` anchor 重复 → 段内工具预算耗尽 | Turn 提前停 | 狂奔：anchor 参数错误不计段预算 + 预算 8 + 内核 nudge（UI-6021） |
| **R7-12** | **mitigated** | 0x567 pool exhausted / 504 每 turn | wall time 长 | 狂奔：LLM 传输重试 4 次 + 指数 backoff（UI-6022）；**无法消除**，离线/换模型仍建议 |
| **R7-13** | **fail** | Round 4 仅 Turn 1/4 | 120s timeout + 脚本 break | 见 §3c · §10 |
| **R7-17** | **done** | 503/pool exhausted **短 backoff 连打** | 故障窗口 dashboard 刷红 | 长退避 30～90s · 少重试 · UI-6023 |
| **R7-19** | **done** | tool loop **无轮间冷却** | 同 key 占 Luna slot | 狂奔每 LLM 轮 1.5s 冷却 · UI-6024 |
| **R7-20** | **superseded** | implementing 段 plan 限 1/turn | T-011 无法二次 plan 收口 | 见 **R7-20b**（限 1 过严） |
| **R7-21** | **done** | `runaway_experience` 遇 timeout 退出 | 只跑 1/4 turn | timeout 续跑 + 默认 180s · UI-6026 |
| **R7-22** | **done** | duplicate continue_key **无限 segment** | segment 2～6 空转 | 同 key 不再续 segment · UI-6027 |
| **R7-23** | **done** | **做了没记账**（V-020 有、T-011 open） | advance 卡 active | VERIFY→TASKS Harness 勾选 · UI-6030 |
| **R7-20b** | **done** | plan_partner **1/turn 不够** | 每任务需 2 次 plan | 默认 **2/turn** · UI-6029 |
| **R7-24** | **done** | `MAX_TURNS=4` 不够 | 15 项剩无法到 verifying | `RUNAWAY_EXPERIENCE_MAX_TURNS=12` · UI-6031 |
| **R7-25** | **done** | Windows GBK 打印 `✓` 崩溃 | Turn 2 脚本 exit 1 | `_console_text` · UI-6028 |
| **R7-26** | **done** | **advance 重置 plan 计数** | Turn 1 单 turn 内连打 plan · T-012→T-024 狂飙 | `begin_turn(reset_plan_cap=False)` on advance · UI-6032 |
| **R7-27** | **done** | **`Insufficient Balance` 误读** | agent 以为不能动 TASKS · 空转 11 turn | gateway 类重试 + 禁 fallback 假提案 · UI-6033 |
| **R7-28** | **done** | **R7-23 只勾 active** | T-014～T-017 有 VERIFY、TASKS 曾 open | `sync_all_runaway_task_checkoffs_from_verify` · UI-6034 |
| **R7-29** | **done** | **active 后空转** | Turn 2～12 反复验 T-014～T-017，T-024/25 未推进 | 续接 prompt 绑定 active + stuck notice · UI-6035 |
| **R7-30** | **done** | **12 turn 上限 vs 队列** | 23/25 停于 `implementing` | 正式队列空 / verifying 即停 · UI-6036 |
| **R7-31a** | **done** | **gateway plan 失败不计 cap** | Round 8 Turn 1 **80+** 次 plan fail 空转 | gateway fail 也 `plan_partner_calls += 1` · UI-6037 |
| **R7-31b** | **done** | **repairing 仍连打 plan** | deliverable 要 PROJECT.md，plan 全挂 | 连续 gateway fail → write_text nudge · UI-6038 |
| **R7-31c** | **done** | **PROJECT.md 无验收命令** | hard verify 报「未定义可执行验收命令」 | `ensure_project_acceptance_section` · UI-6039 |

### 当前 `workspace/test` 快照（Round 8 后）

| 项 | 值 |
|---|---|
| 正式任务 | **25 / 25** done（T-024、T-025 本轮完成） |
| VERIFY | **V-029**（T-024）· **V-033**（T-025） |
| checkpoint | **`repairing`**（曾进 verification，未稳态 `verifying`） |
| 阻塞 | `PROJECT.md` **无** `## 验收标准` + `命令：`…`` |
| S-6011 | **未通过** — Turn 1 空转 ~2h+ 后应停进程再修 |

### 当前 `workspace/test` 快照（Round 7 后 · 历史）

| 项 | 值 |
|---|---|
| 正式任务 | **23 / 25** done（T-012～T-023 本轮新完成） |
| `next_open_task` | **T-024** |
| 会话 active | **T-024** |
| 仍 open | **T-024**（Docker Compose）· **T-025**（发布/回滚） |
| VERIFY | V-017～V-026 已写入；**T-024/25 尚无 V-027/V-028** |
| checkpoint | `implementing`（**未 verifying**） |
| S-6011 | **未通过** — 距 `verifying` 差 2 项正式任务 |

## 4b. 环境债（非 Harness 逻辑）

| 项 | 说明 |
|---|---|
| 0x567 504 | 模型端 pool exhausted / Gateway Timeout；Harness 重试可工作，但 wall time 显著拉长 |
| 0x567 `Insufficient Balance` | **网关偶发误报**，非账户余额；同轮大量 LLM 仍 200 → **R7-27**（重试 + 禁 fallback 假提案） |
| Desktop 冷启动 | T-5907 / S-6001 仍可能被 Electron 壳环境问题阻塞（见 TASKS.md T-5907） |
| 会话 stale | 冷启动时 `active_task` 可能与 TASKS 不一致，需依赖 turn 开始 advance 纠正 |

## 5. 与契约的对照

| 契约（RUNAWAY-FLOW §6） | 当前体验 |
|---|---|
| 没有真实证据不能进入发布等待 | VERIFY V-014 诚实记录 MySQL exit 1，但 TASKS 仍勾选 T-005 → **部分违背推进语义** |
| 任务队列清空触发 verifying | 5/27 完成，大量开放任务 → **未触发**（符合队列非空） |
| 用户界面不展示内部 checkpoint | CLI/脚本可见 checkpoint；Desktop 待 UI-6010 收口 |
| plan 提案狂奔下自动采纳 | **已生效**；副作用是 TASKS 污染（R7-1） |

## 6. 建议实施顺序

```text
R7-1b/R7-2b（done）
  → R7-8/R7-9/R7-10（done）
  → R7-11/R7-12（done / mitigated）
  → R7-13 Round 4（fail · §3c）
  → R7-17/R7-19/R7-20/R7-21/R7-22（R7c · UI-6023～6027 · done）
  → R7-20b/R7-23/R7-24/R7-25（R7d · UI-6028～6031 · done）
  → Round 7 复跑（12/12 · 23/25 · §3e）
  → R7-26～R7-30（R7e · UI-6032～6036 · done）
  → Round 8 复跑（§3f · 卡 repairing）
  → R7-31a～R7-31c（R7f · UI-6037～6039 · done）
  → S-6011 复跑
```

## 3d. 第五/六轮真实 LLM 体验（2026-09-01 · R7c 后）

### Round 5a（Turn 2 崩溃）

| 指标 | 结果 |
|---|---|
| Turn | **2 / 4** 后 **`UnicodeEncodeError`（GBK · `✓`）** |
| 推进 | 7→**9/25**（T-008、T-009、T-010） |

### Round 5b（修编码后 4/4 结束）

| 指标 | 结果 |
|---|---|
| Turn | **4 / 4** · **732s** · 14 工具 |
| 推进 | 9→**10/25**（T-010）；**T-011 卡住** |
| 阻塞 | Turn 2～4：`plan_partner 每回合最多 1 次`；V-020 已有、T-011 仍 open |

**Round 5/6 暴露的新阻塞** → §11 R7d。

## 3e. 第七轮真实 LLM 体验（2026-09-01 · R7d 后 · ~51 分钟 · 12/12）

| 指标 | 结果 |
|---|---|
| Turn 数 | **12 / 12**（全部跑完 · exit 0） |
| 总耗时 | **3066.8s**（~51 min） |
| 工具调用 | **66** |
| 确认弹窗 | **0** |
| 起点 | **11/25** · active=**T-012** |
| 终点 | **23/25** · active=**T-024** |
| checkpoint | **`implementing`**（**未 verifying**） |
| harness notices | **17**（含 1× 180s timeout · 1× SSL EOF） |

**过程摘要**：

1. **Turn 1 狂飙**：Harness 连续 advance **T-012→T-024**；单 turn 内 **>10 次** `plan_partner` 成功（R7-26：每次 advance 调用 `begin_turn()` 重置 plan 计数，使 **2/turn 上限失效**）。
2. **Turn 1 末**：`plan_partner 已达本执行段次数上限`；T-014～T-016 部分 VERIFY/TASKS 收口延后。
3. **Turn 2～12 空转**（R7-29）：active 已是 **T-024**，agent 仍反复跑 T-014～T-017 的 `mvn test` / npm build / VERIFY 静态检查；**T-024/T-025 几乎无实质推进**。
4. **偶发 `Insufficient Balance`**（Turn 2、6、12）：**非账户余额耗尽** — 同轮主对话与多数 plan 调用仍 200；见 §12 **R7-27**。agent 误读为「不能合法改 TASKS」，加剧空转。
5. **Turn 6**：`LLM 请求超时（180s）` — 脚本按 R7-21 续跑。
6. **Turn 12**：SSL EOF 重试后恢复；末次 plan 仍报 `Insufficient Balance` fallback。

**Round 7 结论（纠偏）**：

| 误判 | 实际 |
|---|---|
| 「Turn 12 余额不足需充值」 | 0x567 **网关偶发**返回 `Insufficient Balance`；51 min 内大量 LLM 成功 → **运维上不必充值** |
| 「R7-23 已解决记账」 | 仅对 **active** 任务 sync；batch open 任务仍可能漏勾 → **R7-28** |
| 「12 turn 够跑完」 | 23/25 后 **11 turn 空转**；应 **R7-30** 以队列为里程碑而非固定 turn |

日志：`tools/runaway_experience_last.log`。

## 3f. 第八轮真实 LLM 体验（2026-09-01 · R7e 后 · Turn 1 卡 ~2h+）

| 指标 | 结果 |
|---|---|
| Turn 数 | **1 / 12 未结束**（进程应手动停止） |
| 起点 | **23/25** · active=**T-024** |
| 终点（磁盘） | **25/25** · checkpoint=**`repairing`** |
| plan_partner | **大量 `[tool.fail]`**（R7-27 生效 · 无假提案 adopt） |
| 确认弹窗 | **0** |
| S-6011 | **未通过** |

**过程摘要**：

1. **T-024→T-025** advance 成功；docker/release 文件已写（`release-drill.py`、`RELEASE-DRILL.md` 等）。
2. **TASKS 25/25** · VERIFY **V-029/V-033** 已落盘（plan 失败时靠 **run_command / write_text + sync**）。
3. 进入 **verification** 后 **deliverable_review / hard verify** 失败：`PROJECT.md 未定义可执行验收命令`（缺 `## 验收标准` + ``命令：`…``）。
4. checkpoint → **`repairing`**；agent **反复 plan_partner 修 PROJECT.md**，gateway 全失败。
5. **R7-27 副作用（R7-31a）**：gateway 失败 **不计** `plan_partner_calls` → 同一 Turn 内 **无限** plan 重试（80+ 次）。

**Round 8 结论** → §13 R7f。

## 11. R7d · Round 5/6 收口（UI-6028～6031）

| ID | 问题 | 编码 |
|---|---|---|
| **R7-25** | 体验脚本 Windows GBK 崩溃 | `tools/runaway_experience.py` · `_console_text` |
| **R7-20b** | plan 限 1 导致无法「sanitize + VERIFY 收口」 | `MY_AGENT_RUNAWAY_PLAN_PARTNER_MAX=2` |
| **R7-23** | VERIFY 有证据、TASKS 未勾选 → 不 advance | `sync_runaway_task_checkoff_from_verify` · reconcile 时调用 |
| **R7-24** | 4 turn 跑不完 25 项 | `RUNAWAY_EXPERIENCE_MAX_TURNS=12` · 进 `verifying` 即停 |

```bat
set MY_AGENT_RUNAWAY_PLAN_PARTNER_MAX=2
set RUNAWAY_EXPERIENCE_MAX_TURNS=12
set LLM_TIMEOUT_SEC=180
.venv\Scripts\python.exe tools\runaway_experience.py
```

## 12. R7e · Round 7 收口（UI-6032～6036 · done）

### R7-27 · `Insufficient Balance` 不是余额问题

| 层 | 现象 | 编码方向 |
|---|---|---|
| **Provider** | 0x567 偶发 HTTP/JSON 体含 `Insufficient Balance` | 与 503/504 同类：**长 backoff + 重试**（`is_pool_exhausted_transport_error` 扩展或 sibling） |
| **plan_agent** | LLM 异常走 `_plan_channel_fallback` → **`ok: true` 残缺 proposal** | 狂奔下 fallback **不得 auto-adopt**；返回 `ok: false` + 可重试 notice |
| **Agent 语义** | 主模型把 fallback 摘要当成「永久不能动 TASKS」 | Harness：**VERIFY→TASKS sync 不依赖 plan**（R7-28）；续接 prompt 写明「plan 失败 ≠ 阻塞实现」 |

**验收**：IT-6033 mock `Insufficient Balance` → plan 不重试假提案；sync 仍勾 TASKS。

### R7-26 · advance 不得重置 plan 预算

| 问题 | 编码 |
|---|---|
| `_advance_runaway_checkpoint` → `begin_turn()` 清零 `plan_partner_calls` | advance 路径 **跳过** plan 计数重置，或单独 `begin_turn(reset_plan_cap=False)` |
| Turn 1 连打 plan 触发网关 burst | 与 R7-19 冷却叠加后 wall time 可接受 |

**验收**：IT-6032 单 turn 内 advance 3 次后第 3 次 plan 仍受 cap=2 拒绝。

### R7-28 · VERIFY batch sync

| 问题 | 编码 |
|---|---|
| `sync_runaway_task_checkoff_from_verify` 仅处理 **active** | reconcile / turn 开始：对 **全部 open 行首 `T-*`** 有 VERIFY 证据者 `[x]` |
| T-014～T-017 曾 TASKS open + VERIFY 已有 | 与 R7-23 单任务路径并存 |

**验收**：`test_sync_runaway_task_checkoff_from_verify_batch`（非 active 任务）。

### R7-29 · active 聚焦与空转检测

| 问题 | 编码 |
|---|---|
| Harness 续接 user 未强调 **当前 active** | `_continue_runaway_after_natural_stop` 注入 `T-024` 目标文案 |
| 多 turn 无 `stats.done` 增量 | optional：`project_runaway_stuck_turns` · N turn 无进展 → notice 或 pause reason |

**验收**：S-6011 复跑 Turn 2+ 工具调用应 primarily 服务 active，而非重复旧任务验证。

### R7-30 · 体验脚本里程碑停止

| 问题 | 编码 |
|---|---|
| `RUNAWAY_EXPERIENCE_MAX_TURNS=12` 固定上限 | 默认：**开放正式队列为空** 或 checkpoint=`verifying` → **break** |
| 可选 | `RUNAWAY_EXPERIENCE_UNTIL=verifying` 显式模式 |

```bat
set MY_AGENT_RUNAWAY_PLAN_PARTNER_MAX=2
set RUNAWAY_EXPERIENCE_MAX_TURNS=12
set LLM_TIMEOUT_SEC=180
.venv\Scripts\python.exe tools\runaway_experience.py
```

## 13. R7f · Round 8 收口（UI-6037～6039 · done）

> **先文档、后编码**。Round 8 已证明队列可清空，但 **repairing + plan 网关失败** 可让单 Turn 空转数小时。

### R7-31a · gateway plan 失败计入 cap

| 问题 | 编码 |
|---|---|
| R7-27 仅 `tool_ok` 时 `plan_partner_calls += 1` | **gateway `tool_fail` 也 +1** |
| Turn 1 内 cap=2 失效 | 第 3 次起返回 `plan_partner 每回合最多 N 次` |

**验收**：IT-6037 连续 3 次 mock gateway fail 后第 3 次被 cap 拒绝。

### R7-31b · repairing 阶段禁 plan 风暴

| 问题 | 编码 |
|---|---|
| `repairing` 下 agent 只信 plan 改 PROJECT.md | 连续 **N** 次 gateway fail（默认 3）→ 注入 **一次** user nudge |
| nudge 内容 | **用 `write_text` / `patch_file` 改 PROJECT.md**；`repairing` 下 **不要再调 plan_partner** |

常量：`EXEC_RUNAWAY_PLAN_GATEWAY_NUDGE_MESSAGE` · `MY_AGENT_RUNAWAY_PLAN_GATEWAY_FAIL_MAX=3`

**验收**：IT-6038 repairing + 3× gateway fail → 注入 nudge；不再无限 plan。

### R7-31c · PROJECT.md 验收命令 Harness 自愈

| 问题 | 编码 |
|---|---|
| `parse_acceptance_spec` 为空 → hard verify 失败 | `ensure_project_acceptance_section`：追加 `## 验收标准` + ``命令：`python …` `` |
| 命令来源 | 优先 `.acceptance/**/{release-drill,verify,accept}*.py`；否则项目内首个可执行 `.py` 验收脚本 |
| 调用点 | `_run_runaway_hard_verification` 首次失败且 error=未定义可执行验收命令 → append 后 **重跑一次** |

**验收**：IT-6039 空 PROJECT.md → append 后 `parse_acceptance_spec` 非空。

```bat
set MY_AGENT_RUNAWAY_PLAN_GATEWAY_FAIL_MAX=3
.venv\Scripts\python.exe tools\runaway_experience.py
```

## 14. Phase 59 · bug-fix（后继 R7f）

> R7f（UI-6037～6039）是 **repairing 止血**；根治路径是专用 **bug-fix** 轨，不再依赖 `plan_partner` 修验收。

| 层 | R7f（临时） | bug-fix（Phase 59） |
|---|---|---|
| repairing 入口 | 主 Agent + plan / nudge `write_text` | Harness **spawn `bug_fix`** |
| 计划域 | plan 改 PROJECT（易 gateway 风暴） | **禁 plan**；窄写 PROJECT/VERIFY/代码 |
| 闸门 | `ensure_project_acceptance_section` 占位 append | **T↔V 矩阵 linter** + 语义正确验收命令 |
| implementing | plan cap=2 | 默认 **plan cap=0**（VERIFY sync 由 Harness） |

**文档**：[BUG-FIX-AGENT.md](./BUG-FIX-AGENT.md) · **任务**：T-5960（doc done）· UI-5961～5965 · **手工**：S-5951（repairing→verifying 稳态，S-6011 后继）。

**编码顺序**：M0 矩阵 linter → M1 blocker 扫描 → M2 `bug_fix` 子代理 → M3 `repairing` 接线 + implementing 禁 plan。

## 15. Round 10 · bug-fix 真实 LLM（2026-09-02 · S-6011 / S-5951 pass）

| 指标 | Round 10b |
|------|-----------|
| 起点 | `repairing` · 25/25 · 缺 PROJECT 验收命令 |
| Turn | **1/12**（`UNTIL=verifying` 即停） |
| 耗时 | **~386s** |
| checkpoint 终点 | **`verifying`** |
| bug-fix | Turn 1 开头 Harness spawn |
| plan_partner | **0** |
| 确认 | **0** |

**路径**：`_maybe_run_runaway_repair_lane_at_turn_start` → bug-fix 补 `PROJECT.md` 验收段 → Harness 硬验收通过 → `verifying`。

**Round 10b 尾噪（已修 · UI-5966）**：主 Agent 在 `verification` 阶段调 `run_command` 被误判「不能修改业务代码」。Harness 已通过，不挡里程碑；见 [BUG-FIX-AGENT.md](./BUG-FIX-AGENT.md) §13。

## 10. R7c · Round 4 收口（UI-6023～6027）

| ID | 问题 | 编码 |
|---|---|---|
| **R7-17** | pool exhausted 时 2～15s backoff 仍连打 | `is_pool_exhausted_error` · 少重试 · 30/60/90s backoff |
| **R7-19** | tool loop 连续 LLM 无间隔 | `MY_AGENT_RUNAWAY_LLM_COOLDOWN_SEC`（默认 1.5） |
| **R7-20** | Turn 1 开头 3× `plan_partner` | `MY_AGENT_RUNAWAY_PLAN_PARTNER_MAX=1`（implementing） |
| **R7-21** | 120s timeout 终止整脚本 | 体验脚本默认 `LLM_TIMEOUT_SEC=180` · timeout 不 break |
| **R7-22** | 同 continue_key 重复 `return True` → segment 2～6 | 第二次同 key **return False** |

```bat
set MY_AGENT_RUNAWAY_LLM_COOLDOWN_SEC=1.5
set MY_AGENT_RUNAWAY_PLAN_PARTNER_MAX=1
set MY_AGENT_RUNAWAY_POOL_RETRIES=2
set LLM_TIMEOUT_SEC=180
.venv\Scripts\python.exe tools\runaway_experience.py
```

## 9. R7-11 / R7-12 怎么办

### R7-11 · patch anchor 段预算（UI-6021 · 已编码）

| 层 | 做法 |
|---|---|
| **Harness** | 狂奔下 `find anchor not found / matched N times` **不计**段失败预算；默认预算 **8**（`MY_AGENT_RUNAWAY_SEGMENT_FAILURE_BUDGET`） |
| **内核 nudge** | 首次 anchor 参数失败：提示 `read_file` → `line_range` / 更长 find |
| **未做** | 自动 disambiguate 锚点（改 `patch_file` 语义，风险大） |

### R7-12 · 0x567 504 / pool exhausted（UI-6022 · 已缓解）

| 层 | 做法 |
|---|---|
| **Harness** | 狂奔 LLM 传输最多 **4 次**（`MY_AGENT_RUNAWAY_LLM_RETRIES`），backoff 2→4→8→15s；每 turn 1 条 notice |
| **运维** | 错峰跑 S-6011；504 高峰可换 `0x567-pro` 或对照 DeepSeek |
| **无法消除** | pool 在 provider 侧；只能重试 + 降噪，wall time 仍可能很长 |

```bat
set MY_AGENT_RUNAWAY_SEGMENT_FAILURE_BUDGET=8
set MY_AGENT_RUNAWAY_LLM_RETRIES=4
.venv\Scripts\python.exe tools\runaway_experience.py
```

## 7. 回归与手工验收

| ID | 类型 | 内容 |
|---|---|---|
| S-6011 | 手工 | `workspace/test` · 0x567-flash · 狂奔从 mid-queue 推到 **verifying**，全程 0 确认（Round 7：**23/25 · T-024 active · 未通过**） |
| IT-6032 | 自动 | advance 不重置 plan_partner 计数 |
| IT-6033 | 自动 | `Insufficient Balance` 不产出可 adopt 的 plan fallback |
| IT-6034 | 自动 | VERIFY 证据 batch sync 全部 open 正式任务 |
| IT-6014 | 自动 | plan 提案不得进入 `next_open_task` 结果 |
| IT-6015 | 自动 | 无 VERIFY 证据 / 命令失败时不 advance 到下一 `T-*` |
| IT-6016 | 自动 | 仅正式 `T-*` 开放队列为空时进入 `verifying` |

脚本复现：

```bat
.venv\Scripts\python.exe tools\runaway_experience.py
```

## 16. 开头流程闸门审计（2026-09-03 · UI-6041～6045）

> 真源：[RUNAWAY-STARTUP-GATES.md](./RUNAWAY-STARTUP-GATES.md)

| 批次 | 项 | 状态 |
|------|-----|------|
| 结尾 | UI-6045 bug-fix 可写 `ENV.md` `quality.commands` | **done** |
| 开头 | UI-6041 prep 不绑 requirements intent | **done** |
| 开头 | UI-6042～6044 overlay / 武装 / 路由 | **done** |

**P0 现象**：开狂奔 + 短指令 / Harness 续跑 → `workflow_stage` 卡早期阶段，prep 从未跑；与 overlay「已授权可连续执行」矛盾。

## 17. verification 出口编排（2026-09-03 · UI-6046～6052）

> 真源：[RUNAWAY-VERIFICATION-ORCHESTRATOR.md](./RUNAWAY-VERIFICATION-ORCHESTRATOR.md)

| 现象 | 根因 | 对策 |
|------|------|------|
| 硬验收过、`run_quality` 仍失败 | ENV 无 `quality.commands` | UI-6046 bootstrap |
| 主 Agent 复读「写 ENV 被拒」 | 权责错位 | UI-6047 Harness 短接 + bug-fix 轨 |
| `[Harness]` 仍说「下一项任务」 | 续接文案 | UI-6050 |
| verification 下 `report_progress` 被 G5 拦 | progress_gate 误用 | UI-6049 |

**手工**：S-6046（`workspace/music` 或 test）狂奔至 release_wait，无计划域写撞墙复读。

## 18. v2 真实体验收口（2026-09-07）

本轮使用 `MY_AGENT_RUNAWAY_V2=1`、`0x567-flash`，在 `workspace/music` 会话 `20260818-cef22232` 中从真实项目状态继续狂奔，目标 `release_wait`。

| 项 | 结果 |
|---|---|
| 正式任务 | 36/36 完成；`[P1] T-*` 行可被正确识别 |
| 验证矩阵 | 通过；MX-2 多行 `V-037 → T-103` 绑定误报已修复 |
| 项目验收 | `AC-PROJECT` 通过；API、实时 API、Nginx smoke 和生产构建均通过 |
| v2 phase | `release_wait` |
| checklist | 全部通过 |
| 状态镜像 | `active_task_id` 已清空，`workflow_stage=release` |
| 过程约束 | 无 bug-fix 子代理卡片；未把计划提案写入正式任务队列 |

过程中还修复了正式任务解析器漏识别 `- [x] [P1] T-1007 ...` 的问题；首次验收遇到本机 Docker/质量配置阻塞，放行并补齐质量配置后 `AC-PROJECT` 正常通过。该回合确认了 v2 从 implement 到 verify 再到 release_wait 的真实链路，但不等同于 T-6107 兼容层、进展指纹或 S-UX-028m 已完成。
