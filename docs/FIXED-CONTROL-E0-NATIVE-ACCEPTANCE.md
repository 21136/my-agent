# 固定控制端 E0 原生验收记录

日期：2026-10-24。实验写入范围仍为 `.worktrees/sandbox`。本记录区分原生 E0 启动、一次性 disposable-fixture 原生 adoption、完整生命周期验收、真实 stable adoption 与发行激活；前三者的证据边界不互相替代。

## 授权边界

- 用户已授权真实启动隔离 Electron / sidecar、项目管理式采用，以及模型调用仅限注册 ID `0x567-flash`。
- 不允许助手手工 Git merge 或复制实验源码覆盖稳定树；真实采用必须经项目流程完成。
- 授权不是当前阻塞：实现仍是 `launcher_fixture_v1`，缺少独立、已验证的真实源码绑定和固定实例私有 provider adapter。不得把 fixture binding 解释成真实项目绑定。
- 未读取、复制或输出任何凭据；没有模型调用，调用数为 0。

## 本轮实际结果：原生 disposable-fixture adoption 与一次 normal-quit/reopen 生命周期验收已完成

原生 E0 在同一固定发行实例中完成了一次用户确认的项目管理式 adoption。此结果仅证明 disposable fixture 的原生 adoption 流程，不是对真实项目 stable 分支的 adoption，也不构成新发行激活、模型或完整生命周期验收。

- 当前 run：`artifacts/fixed-control-e0/run-551bc58a-4bde-48e3-a0bc-325f3fd289d8`；固定实例 installation `1058c541-b7e8-422c-9a0c-c8549ac7a788`、instance `7c42334b-5f48-4bdc-8fff-8655a7cdd750`、release `a8856c257b0d63436271349648319f1d6ee4162fd55af34fe1370bc29ff34797`。adoption 后实例仍为 `ready`，identity 未变；198 个 release 文件 bytes 均未变。
- 用户手动关闭了隐私 onboarding modal。parent UI 先展示 no-diff，再展示仅一文件的 diff；parent assistant 按用户既有授权通过原生 UI 确认后，由项目流程在 fixture `source-sandbox` 产生 commit `22d1f34e235dc6e091b24170941f2366f996a2a6`，在 fixture `source-stable` 合并并产生 merge commit `8361318378c72f784f06e0dc5a71713b540f514e`，再同步回 fixture sandbox。两边最终位于同一 merge commit，分别保持 `stable` / `experiment/sandbox` 分支且工作树干净；唯一变化是新增 `docs/native-adoption-probe.md`，SHA-256 `04303a3962db338e2fb013e2828a72dfcfe56c85416b6eab09520cd2ffb32ccd`。stash 状态未变。
- 操作 `8f93924e-48bc-498e-b72a-d574b2b8259b` 于 `2026-10-24T03:43:21Z` 成功结束；journal idle，包含一条成功历史，六项写操作 receipt 均为退出码 0。此前 Escape 取消证明无写入、两个 fixture HEAD 不变且 journal idle；确认操作后的 proof 也确认 only-probe adoption、binding / instance identity 不变、release bytes 不变。模型调用为 0。
- 新鲜真实 stable protection 于 `2026-10-24T03:45:26Z` 检查：稳定树 HEAD、branch、status、tracked set 均未变，981 个 tracked 文件 hash 未变、无 tracked 文件变化。fixture 的 `source-stable` 不能等同真实稳定树；本记录未宣称真实 stable adoption。
- 托盘菜单正常退出证据：`native-normal-quit-proof.json`（checked `2026-10-24T03:57:17.643Z`）为 `pass=true`；instance `7c42334b-5f48-4bdc-8fff-8655a7cdd750` 在 `2026-10-24T03:56:00.873Z` stopped / exit 0，lock 不存在，journal idle，成功 adoption 历史数为 1，launcher exit 0；redacted diagnostic 为 `native-diagnostic-2026-10-24T03-56-00.896Z-33868.json`。Diagnostic stderr 还记录 session contract `active conversation id is invalid` 与加载 `http://127.0.0.1:5174/` 的 `ERR_CONNECTION_REFUSED`；因此此 proof 只证明受控正常退出清理路径，不抹去这些诊断，也不等于 UX 完整通过。
- Reopen proof：`native-reopen-proof.json`（checked `2026-10-24T04:02:44Z`，`pass=true`）记录新 instance `25994675-3ea7-45ad-af6d-72c7048005ac` 于 `2026-10-24T03:59:22.322Z` ready；它与已退出的 instance `7c42334b-5f48-4bdc-8fff-8655a7cdd750` 是不同 generation，但沿用同一 run、installation `1058c541-b7e8-422c-9a0c-c8549ac7a788`、release `a8856c257b0d63436271349648319f1d6ee4162fd55af34fe1370bc29ff34797` 和 binding。Proof 确认 sealed renderer、无 privacy modal、lock owner 匹配、journal idle 且 adoption history 未重放；两个 fixture HEAD/worktree、probe bytes、198 个 release 文件均保持。`native-reopen-observed.txt` 记录 sky UI sealed URL 和“没有待合入的实验改动”。
- `stable-protection-after-native-reopen.json` 于 `2026-10-24T04:03:24.113Z` 证明真实 stable 的 981 个 tracked hashes、HEAD、branch、status、tracked set 均未变。模型调用 0、real stable adoption=false；本轮只接受一次 disposable-fixture 范围内的 normal quit/reopen，不覆盖故障恢复、生产 adoption、发行激活或数据交接。
- 此 continuation 未重跑 tests/build；没有凭据读取/复制、模型调用、真实 stable adoption 或发行激活。

## 验收状态与尚未完成的门

- **一次 normal-quit / reopen 生命周期：已通过（disposable fixture / 固定发行范围内）。** `native-normal-quit-proof.json` 证明原实例正常停止、exit 0、lock 移除、journal idle；`native-reopen-proof.json` 证明新 instance reopened ready，same installation/release/binding，sealed renderer、UI 状态已观察、lock owner 匹配、journal idle 且历史未重放，fixture / probe / release bytes 保持。只接受此次受控正常退出与重开，不等于异常中断/恢复、生产 stable adoption、数据交接、模型或完整产品验收。
- **真实目标绑定实现缺口：仍未解决。** 当前可信 launcher/Host 只接受 `launcher_fixture_v1`，尚无独立、已验证的非 fixture 真实源码绑定入口；本次 native adoption 仅能说明 disposable fixtures 的流程。
- **Provider / model：未接通、未调用。** 先前注册模型授权不改变当前固定实例没有私有 provider adapter 的事实；模型调用数为 0。
- **真实项目 stable adoption、发行激活、唯一数据写入交接：均未接受。** disposable fixture 的 adoption 不代表真实 stable 树改变，也不代表发行/数据切换。
## 聚焦验证与构建证据（不刷新旧总数）

- 生命周期聚焦回归：35 passed、0 failed、0 skipped。
- preparation 聚焦回归：21 passed、0 failed、0 skipped。
- 物理文件系统回归：19 tests、18 passed、0 failed、1 skipped（sandbox volume 不支持 junction）；另有物理路径完整树测试 3/3 passed。
- 固定控制端构建退出码 0；依赖审计检查 157 个 locked packages。构建输出仍包含 chunk-size、module-type 及不支持的 bundler option 警告；这些不是构建失败。
- 上述均为聚焦测试/构建证据，不等于用户体验完成或 stable adoption。



## 证据索引

根目录：`artifacts/fixed-control-e0-20261024/native-acceptance-20261024T1053/`

- `authorization.json`、`prepare-physical-fs.txt`、`physical-native-kit-check.txt`、当前 run 的 `control-state/instance.json`
- `native-normal-quit-proof.json`、`native-diagnostic-2026-10-24T03-56-00.896Z-33868.json`、`native-reopen-proof.json`、`native-reopen-observed.txt`、`stable-after-native-reopen.json`、`stable-protection-after-native-reopen.json`
- `native-ui-observation.json`、`stable-after-native-ui-observation.json`、`stable-protection-native-ui-observation.json`；`native-adoption-before.json`、`native-adoption-cancel-proof.json`、`native-adoption-confirmation-observed.txt`、`native-adoption-after.json`、`native-adoption-proof.json`、`native-adoption-result-observed.txt`；`stable-after-native-adoption.json`、`stable-protection-after-native-adoption.json`
- `lifecycle-regression-parent.txt`、`preparation-regression-parent.txt`、`physical-fs-regression-parent.txt`、`physical-fs-green-complete-20261024T112209443.txt`
- `fixed-control-build-after-native-fixes.txt`、`physical-build-exit-code.txt`、`model-and-real-adoption-readiness.json`、`prepared-fixture-adopter-readonly-status.json`
