# 全栈演化切片 A：T1–T7 本地实施进度

日期：2026-10-22（Asia/Shanghai）  
分支：`experiment/sandbox`  
写入范围：仅 `F:/my-agent-main/.worktrees/sandbox`。本记录是实现进度，不是独立审查或 A 总体验收。

## 当前结论

在 T1–T6 sandbox 代码和聚焦验证基础上，T7 已接入当前 Agent/Executor 的 builtin、Session 元数据关联、演化 overlay、按绑定状态执行的 fail-closed 工具策略和受监督客户端调用封套。新绑定只存 `evolution_id`；host run/authorization 引用是进程内上下文，不进 `SessionMeta`。

T7 的新增行为测试和 session-agnostic 工具回归最后一次结果为 **23 passed，3 个 subtests passed**。但计划列出的 `test_sandbox_write_scope.py` 尚未运行：它创建系统 `TEMP` 目录，实施计划要求先批准具体 C: 临时路径；本轮没有该授权。因此 T7 的 GREEN/检查点仍是部分完成，不能宣称整项通过。

## T7 代码范围

- `agent-core/session.py`：`SessionMeta.evolution_id` 兼容旧 meta；`ExecutorSession` 同步绑定，binding 变化时清除旧授权引用；host run 与授权引用不持久化。
- `agent-core/evolution/policy.py`：在 proxy rewrite 之后拒绝活跃演化会话中的 `run_evolved`、命令/写操作、上下文切换、网络/生图、子代理和未知工具；只保留受限只读 builtin 与 `evolution`。拒绝 `host:<id>` 主机根读取。
- `agent-core/evolution/context.py`：Supervisor 确认当前 conversation/evolution 后才 attach；overlay 只输出 allowlist 投影字段，不拼接候选文本或 token。稳定 lifecycle 的 32-hex run id 仅规范化成协议 UUID；非法 variant 不放行。
- `agent-core/tools/builtin/evolution.py`：严格 action/payload 封套，conversation/evolution/host-run/auth 不从模型参数读取；ask/read_only/plan_only 只允许 inspect；没有可信 grant/client 时副作用失败关闭；unknown 不宣称成功、不自动重试。
- `agent-core/tools/executor.py`、`agent-core/agent.py`、`agent-core/tools/registry.py`：真实 builtin 注册/注入，policy 运行在 proxy rewrite 后、执行/确认前；稳定 Host 尚未注入 client/run grant，缺失时安全拒绝。
- `agent-core/loader.py`、`evolve/tool-catalog/INDEX.md`、`docs/TOOLS.md`、`docs/MAP.md`：tool summary、发现索引与注册文档更新。索引预算按计划的 **2048 个字符**检查；原有回归误用 UTF-8 文件字节，基线为 2145 bytes/1471 chars，本轮将断言更正为字符数并保留预算检查。

## 可复核证据

- [T7 定向 Python + session-agnostic 回归输出](../../artifacts/evolution-a-t5-followup-118425f1247f4fefa3f195deccd5a7b2/t7-focused-python.txt)
- [T7 Python runtime/import preflight](../../artifacts/evolution-a-t5-followup-118425f1247f4fefa3f195deccd5a7b2/t7-python-preflight.txt)
- T5/T6 前序日志及控制发行证据见 [T1–T6 进度记录](2026-10-22-evolution-t1-t6-implementation-progress.md)。

## 仍未通过 / 未运行

| 层 | 状态 |
|---|---|
| `test_sandbox_write_scope.py` | `not_run`；需要用户批准具体 C: 临时目录，避免测试写入未批准的系统 TEMP |
| T7 稳定 WsBridge/Host 中的真实 EvolutionClient、lifecycle host_run_id 与用户授权注入 | 留 T8；当前用 fake client 验证封套，不是服务端接线 |
| 真实模型是否选择并正确使用 evolution builtin | `not_run`，留 T9 M1 |
| 外部审查 / 安全审计 | `incomplete` / `not_run` |
| 真实 named pipe / 候选进程 / Electron Host / preview | 仍未运行；P1/H1 未授权 |
| 完整桌面 build、依赖安装/审计、真实 UI | 本轮未运行；T1–T6 记录的 dependency/preview 阻断没有因此转为通过 |

**下一步**：继续 T8 的真实 Session/WsBridge 接线与测试 Host 隔离。纯 `main-bridge` / `test-profile` / `evolution-state` 模块及其 Node 定向测试已作为后续 T8 起步补入；见 `2026-10-22-evolution-t1-t8-implementation-progress.md`。未授权的 P1/H1/M1 运行保持关闭；不 stage、commit、merge 或启动稳定/预览端口。
