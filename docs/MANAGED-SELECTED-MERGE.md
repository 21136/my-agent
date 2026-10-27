# 选定源码合入：用户与开发说明

## 做什么

这条流程把一次**明确选择的 sandbox 文件内容**，经受审查的工程入口，合入 stable 项目；它不是把 `experiment/sandbox` 分支或整段提交历史合进来。

1. **审查**：读取 stable 与 sandbox 的源码、Git 身份、HEAD/index、历史和允许范围清单，呈现已有阻断。
2. **选择**：用户逐路径声明本次范围。选择是范围声明，不是归属证明；未选文件不会被自动带入。
3. **保存检查点**：重新核验当前审查与选择，保存被选 sandbox 文件的原始字节、哈希、模式及绑定信息。检查点不是 Git commit，也不是合入授权。
4. **准备项目合入计划**：项目从固定检查点重读内容，重新检查两个 worktree、对象/索引身份、保留范围和每个 selected path 的 stable 当前状态，计算预期 tree/commit。
5. **精确原生确认**：Electron 警告对话框逐项展示路径、stable 合入前哈希/模式、选中内容合入后哈希/模式、blob 身份、动作以及两个 worktree 身份。按钮明确为“通过项目 Git 合入所选内容”；确认后宿主再次读取检查点并重算计划，内容或计划有变化即停止。
6. **项目执行与 source-only receipt**：可信宿主使用临时 index 构造只包含所选内容的 tree 和单父 commit，再对 stable 执行 fast-forward-only 合入。有明确原生确认的原始工作字节保留计划时，先构造并 fast-forward 到保留父提交，再 fast-forward 到所选内容子提交。执行后核对 stable HEAD/tree、所选字节和模式，以及 sandbox 与未选范围的保留状态；成功回执仅表示 `source_merge_verified`。

**不做**：不合入 sandbox 的整段历史；不自动把 stable 同步回 sandbox、不切换主壳 URL、不激活候选版本或数据；不调用模型。合入会真实修改 stable 项目的 Git HEAD，并可能向共享 Git object database 写入新的 blob/tree/commit 对象。

## Stable 重叠与拒绝条件

默认准备接口仍拒绝选中路径的 stable 工作字节差异。可信原生宿主可显式启用 `preserveStableWorkingChanges: true`，仅为已存在且模式不变的选中路径准备原始工作字节保留计划；renderer 不能传入该选项。选中路径有暂存/index 差异、index-only 条目、新路径碰撞、缺失/不安全类型或模式差异时仍拒绝。流程不会自动选择 ours/theirs、清理或扩大选择。与本次选择无关的允许源码变更按计划绑定，执行前后均须核验。

保留计划逐路径绑定原始字节、完整 SHA-256、Git blob OID 和模式，并计算保留 tree/commit。原生确认默认取消，明确披露：**这些选中的既有未提交修改将成为保留父提交中的已提交历史，不再保持未提交状态**；所选 checkpoint 内容成为其子提交。确认前必须完成语义整合审查；保存原始字节不能证明功能整合正确。

新执行的 clean 或原始字节保留操作，store 均在任何 Git 写入前独占、持久写入并回读 `intent.json`、完整 `plan.json`、`checkpoint.json` 和原始 `stable-index.before`。有保留阶段时，只将已确认的选中原始 blob 写入真实 index，再执行两次 fast-forward；不会暂存未选路径，不使用 stash/reset/checkout。原始 base64 字节不进入 renderer API 或原生展示。失败或不确定结果保留证据并进入 maintenance。

成功后可继续使用[合入后源码来源核验](MANAGED-POSTMERGE-SOURCE.md)。该入口只核验实际提交的元数据，不构建、封存或激活发布；旧 v1 记录缺少完整持久证据时拒绝，不自动补写。

源码保留能力**不代表整体只剩真实使用验收**：归属/保留审查、真实项目合入后的发布重建与来源封存、写入 fencing、持久激活/维护恢复/LKG，以及私有模型适配和预算等仍是独立工作。

当前 stable 中实测有四个与 sandbox 工作重叠的 dirty 文件：

- `desktop/electron/main.ts`
- `desktop/electron/preload.ts`
- `desktop/src/api/ws.ts`
- `desktop/src/shells/unified/index.ts`

本轮在 sandbox 整合了这四个文件涉及的会话分区、连接生命周期及宿主能力协议，相关功能回归证据见 `artifacts/continuous-closeout-20261025/`。**真实 stable 的四个 dirty 文件尚未采用新内容**，这些路径也不是默认 selected paths。真实采用仍须明确逐路径选择、审查依赖与归属，并经过上述原生精确确认。只读工程预检或隔离 Git 验证不能替代真实采用；不得据此宣称当前没有真实冲突。

## 真实入口与原生验收边界

实际操作入口必须是**加载 sandbox 新版 `main` / `preload` 的 sandbox Electron 自身工作台**，然后进入：

`壳开发预览 → 只读源码审查 → 选择/核验 → 保存检查点 → 通过项目合入此检查点`

浏览器页面或 stable Host 仅切换 sandbox renderer URL，不会升级 Host 正在运行的 main/preload，也不能证明上述原生确认/执行链已接通。工程代码和离线验证通过不等于 sandbox Electron 原生入口已经实际验收；本说明不宣称做过本机原生操作验收。

## Unknown、单次执行与维护

store 对同一计划永久单次使用，并串行化所有计划。成功 receipt 必须是精确的 13-key `managed_selected_merge_receipt_v1`，绑定计划/检查点、完整 selected paths、stable 前后 HEAD 和 merged tree；`sourceMergeExecuted=true`、`sourceHistoryMerged=false`、`readyForLaunch=false`。receipt 不是应用启动、发布或数据迁移凭证。

已有未知项、链接/硬链接、部分 intent、失败/缺失/不匹配 receipt、未验证成功 receipt、超限或执行结果不确定都进入 maintenance。**不要重试、接管、删锁、删除未知材料或自动恢复**；必须由另行审查的人工维护/恢复流程处理。确认后的失败不作为“再点一次即可”的普通重试。

## 空间边界

- store 最多 **8 个 operation 目录**，所有该 store 内受管记录文件的合计上限为 **32 MiB 逻辑文件字节**。
- 临时 `temporary.index` 的保守上界为 **4 MiB**；engine 在写入前按计划上界校验，实际大小还会在 finish 时复核。
- 原始工作字节保留每文件最多 **4 MiB**、合计最多 **8 MiB**；规范化计划和检查点各最多 **16 MiB**，原始 index 最多 **1 MiB**。这些记录仍受同一 store 聚合预算约束，不是额外额度。
- 这些只是 selected-merge **操作记录 store 与临时 index** 的边界，**不是**整个双版本源码/依赖、整个 Git object database 或整次合入磁盘增量的上限。新的 Git 对象写在项目共享 object database，不计入上述 store 聚合；因此不能把“32 MiB”表述成总增量保证。
- F: 当前按 **128 KiB allocation cluster** 估算时，小文件会因簇取整而产生比逻辑字节更大的分配占用；目录与文件系统元数据也另计。逻辑上限、按簇估算与双版本总磁盘占用是不同口径，不可互换。

## 代码入口

- `desktop/launcher/managed-source-selection.mjs`
- `desktop/launcher/managed-source-checkpoint.mjs`
- `desktop/launcher/managed-selected-merge.mjs`
- `desktop/launcher/managed-selected-merge-store.mjs`
- `desktop/launcher/managed-stable-preservation.mjs`
- `desktop/electron/managed-selected-merge-bridge.ts`
- `desktop/electron/main.ts`
