# 探索工具结果写入冲突：诊断与修复

首个支持的故障是 `explore` 已成功返回，但会话保护拒绝追加工具结果。它不是跨日切换故障，也不表示原工具应该重跑。其他工具的写入异常不自动归入这个故障。

## 使用

1. 从工程目录构建独立验证镜像（需要本机 Docker Desktop 的 Linux 引擎）。

   ```powershell
   docker --host npipe:////./pipe/dockerDesktopLinuxEngine build -t my-agent-incident-verifier:local tools/incident-repair-verifier
   $env:MY_AGENT_INCIDENT_TEST_IMAGE = (docker --host npipe:////./pipe/dockerDesktopLinuxEngine image inspect my-agent-incident-verifier:local --format '{{.Id}}').Trim()
   ```

2. 从同一个 PowerShell 窗口启动桌面应用，让 sidecar 继承该环境。修复模型固定为已配置的 `0x567-flash`。正常使用不需要开启自我改造的其他策略。
3. 当该故障出现时，从当前会话的 **工作动态 → 诊断并修复** 启动。不自动重跑原工具，不要求逐步确认。同一个故障仍要属于当前连接和当前会话；旧界面的请求不会在重连后被当成新授权。

## 默认与可选发布

- 默认只生成候选。没有有效镜像、模型配置或正确复现时，不生成或写入代码；也不会回退到直接在主机运行模型生成的 Python。
- 若要允许修复写入稳定区及其已登记的 `.worktrees/sandbox`，启动前可设置 `MY_AGENT_INCIDENT_PUBLISH_ROOT` 为稳定工程的规范绝对路径。正式使用前应先在隔离仓库演练；清掉该变量恢复默认的仅候选模式。
- 两区分别以自己的 Git HEAD 复现原故障，并用同一组锚点替换分别验证。两区目标文件都要与检验时一致且无正在编辑的变更。工作区其他改动不纳入修复；有冲突时优先保留候选，不强行覆盖。
- 发布仅写入两区定向文件，不提交、不推送，也不会重启正在处理会话的后端。发布成功后须重启对应后端。跨两个工作区的文件写入不是 Git 原子事务；若失败，只尝试撤回本次可识别的写入。并发编辑导致无法恢复时保留恢复材料并报错。

## 证据和验收

`data/incidents/<id>.json` 只保存故障类型、阶段、工具、会话请求 ID（如有）、源码版本摘要及脱敏栈，不存消息、工具参数、工具结果或绝对路径。候选另存 `<id>.candidate.json`；有未确认的并发写入时，原文件恢复内容另存 `<id>.recovery.json`。不把上述文件放进对话历史。模型只接收许可范围内的已提交源码和固定故障类别；不会收到故障日志文本。

保护用例 `agent-core/tests/test_incident_repair_oracle.py` 走真实 Session 和 `ToolExecutor → SubagentRunner → read_file` 路径，替换的只有供应商请求。旧版必须因误修复正在执行的调用而触发唯一失败；新版还必须保留真实崩溃后的孤儿工具记录修复。这不是任意故障的通用自修系统。
