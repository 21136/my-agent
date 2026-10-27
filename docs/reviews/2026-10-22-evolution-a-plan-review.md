# 全栈演化切片 A：实施计划交付记录

日期：2026-10-22，Asia/Shanghai。

## 授权与交付

用户确认详细设计并要求制定实施计划。本轮只编写计划、记录设计确认状态和进行文档静态检查；没有开始产品实现，没有模型调用、候选试跑或稳定采用授权。

- [实施计划](../superpowers/plans/2026-10-22-evolution-slice-a-implementation.md)：T1–T9，文件写集、生产/消费接口、失败测试示例、聚焦命令、授权边界和验收映射。
- [主设计](../superpowers/specs/2026-10-22-stable-led-full-stack-evolution-design.md) 与 [契约附录](../superpowers/specs/2026-10-22-stable-led-full-stack-evolution-contracts.md)：仅把头部状态从待评审改为用户已确认、进入计划阶段；没有更改架构内容。
- 原设计自检记录保留，不覆盖其历史结论。

本计划只细化 A（同一控制会话在候选失败后继续修复），并列出 B 保护后端、C 真实新工具与内核替换、D 共享现场、E 正式采用的接口和前置门槛。它们不是被取消，也不是 A 通过后自动完成。

## 编写者自检修正

1. 修正新 Node 测试到 sandbox/artifacts 的相对导入层级，避免误加载稳定目录。
2. 将 main-bridge 设计为无 Electron 运行时 import、显式注入 IPC adapter，统一独立构建与普通 Node 测试路径；renderer 纯状态测试另用 Node type stripping 直接导入。
3. 绑定/授权是同一 journal 的可重建投影；intent 与 revision 递增在同一条 durable 记录中写入，避免第二个可变真源和接口循环依赖。
4. 补全 test-profile 写集、Python import 引导、pytest 独立 basetemp、EVOLUTION_TEST_ROOT 和既有回归的临时目录授权边界。
5. helper 的 unknown 结果从原可信请求保留身份，不把残缺/非法 ID 当完整 ControlResult；统一 A payload parser 的任务归属和 CaseSpec 枚举。
6. 明确独立 Host 的 app/data/profile、动态端口零、真实 preload 和固定源码包；只改 userData 不算与真实仓库数据隔离。
7. 模拟、真实进程脚本、真实控制模型修复分别记账；A 的受管叶子进程不宣称强隔离或通用进程树回收。
8. 首次静态映射检查发现 AC-06/07 等缩写不能逐项提取，改为显式 AC-06/AC-07；保留首次检查结果，不伪装为首轮全绿。

## 本轮实际验证

证据目录：`artifacts/evolution-plan-review-057bc9c2bc9343239523f122fc1cb9a9/`。

- [最终静态结果](../../artifacts/evolution-plan-review-057bc9c2bc9343239523f122fc1cb9a9/documentation-static-validation.json)：passed，仅限文档。
- [首次检查记录](../../artifacts/evolution-plan-review-057bc9c2bc9343239523f122fc1cb9a9/documentation-first-pass.json)：保留 AC 标识提取问题。
- UTF-8、围栏闭合、尾随空白、本地 Markdown 链接、已有接入路径、9 项任务编号检查通过。
- 15 个 EV 需求、20 个 AC 用例均映射到 A 或明确后续切片；映射完整不表示功能已经满足。
- 26 个代码块静态检查通过：JavaScript 6、TypeScript 7、Python 3、JSON 2、PowerShell 8。
- JavaScript 使用 node --check；TypeScript 使用 createSourceFile.parseDiagnostics，仅语法，不是 tsc 类型检查；Python 使用 ast.parse；PowerShell 使用 ParseInput。未执行示例 import、测试或产品命令。
- Python 预检确认解释器为 F:/my-agent-main/.venv/Scripts/python.exe，且本次文档检查所需标准库可导入；不据此推断所有产品依赖完整。

## 仍未验证或未关闭

| 项目 | 本轮口径 |
| --- | --- |
| 产品功能/单元/集成测试 | 未运行；文档中的 RED/GREEN 是未来命令 |
| 依赖与产品构建 | 未验证；既有依赖/构建阻断未因本轮文档检查关闭 |
| 真实预览/真实 Electron Host/preload | 未运行 |
| 真实候选脚本恢复 P1 | 未运行，需明确 local_managed 风险与范围授权 |
| 真实控制模型同会话修复 H1+M1 | 未运行，需 Host 启动与模型/输入/预算授权 |
| Windows Sandbox/隔离负向验收 | 未运行，属于 B |
| 候选新增工具/内核替换 | 未实现，真实模型验收属于 C |
| 场景标注/发送/恢复 | 未实现，属于 D |
| 外部独立审查 | 仍 incomplete；外部审查代理未能运行不算通过，本轮未派发代理 |
| 安全审计/稳定采用/数据迁移 | 未进行 |
| Git 交付 | 未暂存、未提交、未推送、未合入；无产品源码修改 |

## 执行交接

默认在本会话按 T1→T9 依赖顺序执行，每任务聚焦验证；不默认全量测试。只有用户明确要求委派时才使用 6luna 子代理。用户批准实施后先做 T1；P1/H1/M1 依授权分别推进，未运行层级保持 partial。当前停止于计划交付。
