# 自动模式（runaway）下线记录 · 未来无任务循环设计备忘

> 状态：**已删除**（2026-10-18）。自动模式（runaway v1/v2、计划代理、进度门、task-stop）随任务子系统一起移除。
> 计划以后重建为 **不依赖 TASKS.md 的循环**；本文件只保留值得继承的设计思想，不是现行规范。

## 删除了什么

- 后端模块：`plan_agent.py`、`plan_tools.py`、`plan_patch.py`、`progress_gate.py`、
  `runaway_flow.py`、`runaway_lease.py`、`runaway_verification.py`、`runaway_verify_matrix.py`、
  `runaway_v1/`、`runaway_v2/`、`tools/builtin/plan_partner.py`、`evolve/tools/project/report_progress/`。
- 会话字段 `project_runaway_*`（旧会话里的这些键在加载时被忽略，不会报错）。
- WS 消息：`project.runaway.set`、`plan.response`、`project.task.*`、`project.plan.*`。
- 桌面端：顶栏「自动」开关、任务/计划侧栏页。

源码可从 git 历史取回：`git show <baseline>:agent-core/runaway_v2/acceptance.py`。

## 要继承的思想：「退出码即真相」（runaway_v2/acceptance.py）

原 `run_acceptance()` 的核心约定：

1. **完成与否只由可执行检查决定**。每个检查项都归约为 `HookResult(ok, exit_code, command, tail)`；
   `ok` 只来自真实命令的退出码（或等价的确定性检查），**不来自模型的自述**。
2. **失败要带证据**：保留执行的 `command` 与输出末尾 `tail`（约 20 行非空行），
   下一轮把它原样喂回模型，而不是让模型“回忆”哪里错了。
3. **未知 = 失败**：无法识别的检查类型返回 `ok=False, exit_code=1`，绝不默认通过。
4. **超时有上限**：检查本身有 wall-clock 超时（原 `hook_timeout_sec()`）。

## 未来循环的草图（无任务清单）

```
loop:
  agent 改代码（普通会话，无门禁）
  run verify command(s)   # 来自 PROJECT.md / VERIFY.md 的验收命令，或用户指定
  if all exit_code == expected: stop("通过")
  else: feed {command, exit_code, tail} back; continue
  stop on: 用户停止 / 预算（轮数、时长）用尽 / 连续同一失败指纹 N 次
```

- 不读、不写 `TASKS.md`；进度就是“最后一次验证的结果”。
- 不需要计划确认、一项一停或进度门；安全护栏（项目根写范围、跨项目阻断、write_policy、
  write_evolve 阻断）照常生效。
- 指导性内容放在 `evolve/skills/plan-and-verify/SKILL.md`（只提供方法，从不设门）。
