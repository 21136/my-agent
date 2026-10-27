# 合入后源码来源核验

本入口核验 selected merge 的持久成功证据与实际 Git 提交，并返回合入后已提交源码的元数据清单。它是 E1 发布准备的一道门，**不是构建、发布封存、生产 Host 上下文或激活实现**。

```text
node desktop/launcher/managed-cli.mjs --capture-selected-postmerge <stableRoot> <sandboxRoot> <planDigest> <absoluteGitExe>
```

参数必须明确提供两个源码根、64 位计划摘要和本机 Git 可执行文件绝对路径。CLI 只接受宿主固定路径（Windows 为 `C:\Program Files\Git\cmd\git.exe`），模块同时执行规范路径、本机可执行文件和元数据安全校验。入口只从指定 sandbox 的 `artifacts/managed-selected-merges/<planDigest>/` 读取完整 v2 操作证据；不会接受任意 JSON 文件路径。旧 v1 receipt 缺少持久计划/检查点/index 证据时拒绝，不自动补写或重新执行合入。

核验包括：

- 全 store 无未知/部分操作或占用锁，完整计划、检查点、原始 index、intent、成功 receipt 的规范格式、摘要与相互绑定一致。
- 当前两个源码根、分支、共享 Git 元数据身份符合计划；实际 stable HEAD/tree 等于成功 receipt 的结果。
- 存在原始工作字节保留时，实际保留父提交的 parent/tree/blob/模式/字节与计划一致；最终所选内容提交的父链一致。
- 选中路径当前工作字节、模式及 index 与合入后的已提交内容相符。
- 从实际 merged tree 读取允许源码的原始 Git blob，生成路径、模式、blob OID、SHA-256 和字节数清单；不将无关未提交工作文件当成发布输入，不读取受保护路径的 blob。
- 使用合入流程的本机 Git 与元数据安全校验，并在读取结束后复核身份，漂移即拒绝。

标准输出只给出摘要和文件数。API 返回冻结的 `managed_postmerge_source_snapshot_v1` 元数据记录，固定 `readyForLaunch=false`、`buildVerified=false`；没有原始保留字节、凭据或数据目录内容。此清单没有把源码复制到发布目录，也没有运行构建。后续构建必须从这些已绑定的 blob 建立独立输入，再核验输入、输出和 release provenance，不能直接把当前开发目录的构建称为 postmerge 发布。

每个源码 blob 最多 4 MiB，合计最多 64 MiB，最多 20,000 个文件；Git 清单、批量输出和单次命令另有限额。拒绝不支持的模式、子模块、路径别名、链接、配置间接读取、对象替换以及不确定证据。拒绝不触发修复、清理、重试、旧数据读取或 namespace 状态推进。

当前验收边界：隔离 Git 操作的成功证据可用于这道门的工程验证。后续受控源码物化、实际 TypeScript/esbuild/Vite 构建、生产格式封存和再次校验已有独立实现与隔离链路证据，见 [MANAGED-POSTMERGE-RELEASE.md](MANAGED-POSTMERGE-RELEASE.md)。真实项目尚未进行本轮原生精确确认和源码合入，不能据此称真实 postmerge 已验收。生产上下文、运行时绑定、首个空库的 fenced writer、初始化、active/observing/LKG 与维护恢复仍需继续实现。
