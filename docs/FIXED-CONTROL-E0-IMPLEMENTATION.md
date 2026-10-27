# 固定控制端 / Launcher E0：实现与验收记录

日期：2026-10-24。范围：`F:/my-agent-main/.worktrees/sandbox`。

## 结论边界

本轮实现的是双业务版本之外的**固定控制底座**，不是把实验版手工合入稳定版，也不是增加第三个日常业务版本。源码交付、运行发行、数据交接是三个不同状态；E0 不把“源码已合入”显示为“新程序已生效”。

目前已有代码级、真实临时 Git、构建，以及**真实隔离夹具准备成功和包内 Launcher 静态校验通过**的证据。此前系统 Node 目录链接阻断已通过 sandbox 内完整便携 Node 发行目录解决，没有修改系统链接或放宽 gate。**仍没有 Electron/sidecar 原生验收证据**，不得据此声称“一键采用并换代”的全链路可日常使用。

## 已实现的控制链

- 独立 Launcher 入口，严格 fixture-only 绑定、完整字节清单、内容寻址发行、容量检查和不覆盖发布。
- Node / Electron / venv Python / Python base 四个 tree-scoped 运行时指纹；不从可变业务源码查找控制模块。
- stdin 私有身份握手、直接持有子进程句柄、单实例锁、超时/异常进入维护，不自动偷锁、重放或重启。
- main 共享显式目标 adopter 与恢复 journal；首次 Git 写前持久化基线和意图，精确记录备份对象与执行回执。
- 固定 renderer 与实例 data/profile/workspace 分离；预览不继承采用权限，固定 Host 不回退到可变 Vite 主壳。
- Python 冷启动以 `-I -B -S` 先核对布局、解释器及导入根，再允许导入 server；不执行 `.pth`，不写发行 pycache。常规 Python 启动路径保留。
- E0 冻结 evolve 为只读，禁用聊天、通用 command、proposal 接受/拒绝和 companion 同步；不接入 provider/凭据，不调用模型。
- 源码合入只针对专门新建的 disposable Git worktree；真实 stable/sandbox 仓库及真实 data 不能被 fixture 绑定接纳。

## 最终验证账本

| 检查 | 当前证据 | 限制 |
| --- | --- | --- |
| 新 Launcher/context/adoption/prepare/跨语言 suite | 64 项：63 passed、0 failed、1 skipped | 链接创建因 F: 卷能力跳过；已在最终代码上重跑 |
| 既有 adopter / renderer 定向回归 | 31 passed、0 failed | 临时真实 Git，不是当前仓库合入 |
| 新 Python layout / cold entry | 25 项：23 passed、0 failed、2 skipped | 两项真实链接检查不可执行；server loader 注入，不启动服务 |
| 既有 evolution WS / 私有引导兼容 | 10 passed | fake client/agent，未运行候选、模型或命名管道 |
| 扩展 external-project / host-scope 兼容尝试 | 8 项 setup errors | 原隔离 helper 必须创建 agent-core symlink/junction；在产品断言之前失败，不记为通过 |
| Electron strict TypeScript | exit 0 | 不代表原生应用启动 |
| desktop build | exit 0；依赖审计 157 locked packages checked | 保留原有 bundle/chunk/option warnings；构建回执已验证与当前 148 项输入、97 项输出一致 |
| stable 保护核查 | 981 个 tracked 文件 hash、HEAD、tracked set、原 dirty status 均未变 | 最终代码收尾后再次比对通过 |

原始输出在 `artifacts/fixed-control-e0-20261024/`。其中失败与 RED 日志也保留，不能将全部日志都当通过记录。

## 真实准备尝试及环境阻断（此前失败记录）

执行过 `prepare-fixed-control.mjs --prepare`，结果为拒绝：`tree contains link: git.exe`。

已核实 `C:/Program Files/nodejs/git.exe` 是指向 `C:/Program Files/Git/cmd/git.exe` 的符号链接。完整运行时清单不接纳此链接；本轮不删除、修改它，也不放宽 gate 或伪造单文件完整运行时。

最终代码上的第二次准备仍在同一链接处拒绝，且未新建 run 或复制 payload。最初失败时留下的受标记目录 `artifacts/fixed-control-e0/run-266c3d41-8630-463a-afb0-0dc5f74a5b4d` 保留供核对。当时没有可用 binding 或真实 kit 的 `--check` 成功记录；后续已完成下节准备与静态校验。两阶段均没有执行 `--launch`。

## 后续运行时准备与静态验收（2026-10-24）

本次续办仅准备运行时和 disposable fixture，不修改产品代码或稳定入口，不重跑既有大套测试、不重建未改变的产物。构建回执仍匹配当前 148 项输入和 97 项输出。

- 下载完整官方 Node `v24.15.0` Windows x64 ZIP，并与官方 HTTPS `SHASUMS256.txt` 核对。SHA-256：`cc5149eabd53779ce1e7bdc5401643622d0c7e6800ade18928a767e940bb0e62`。未核验发行签名，不能称为签名来源或可复现构建证明。
- 解压辅助脚本先校验全部 ZIP 路径、类型、冲突、大小和容量，再以排他新建方式提取；8 项合成定向测试通过。真实解压后的 1,807 文件、440 目录与 ZIP 全量逐文件比对，无缺失、额外路径或哈希差异；不是只复制 `node.exe`。
- 使用该完整运行时自己的 `node.exe` 执行 `--prepare`，退出 0。产生 197 项 payload、8 项独立 Launcher 文件；四个 runtime tree 均纳入完整字节清单。Node 在 sandbox；Electron、venv Python 和 Python base 仍引用已指纹记录的本机目录，**不是可搬到任意机器的完整安装包**。
- 再以同一 Node 执行生成包内 `launcher/cli.mjs --check`，退出 0，`state=verified`、`node_runtime_matches=true`。校验会运行只读 Git 元数据子进程，但不启动应用、sidecar、候选或模型。
- 隔离 Host 的 data/workspace/profile 仍为空，控制目录只有 binding；两个临时源码分支仍指向同一个初始提交，工作区干净。真实仓库没有 Git 写入。
- 续办后重新核对 stable：981 个 tracked hash、tracked set、HEAD、原 dirty status 均未变；系统 Node 二进制哈希及原 Git 链接/目标也未变。

当前可用 run-root：`F:\my-agent-main\.worktrees\sandbox\artifacts\fixed-control-e0\run-c3d18b74-ed7f-4d4f-9e92-163b6c0e32f5`。发行 ID：`cce6f66f86395ac0bc02e0d4fa2083f50e866b1e79590d666bade7b616edc349`。

新证据目录：`F:\my-agent-main\.worktrees\sandbox\artifacts\fixed-control-e0-20261024\runtime-preparation-20261024T1028`。重点文件：`runtime-preparation-summary.json`、`download-verification.json`、`node-complete-distribution-verification.json`、`extraction-tests-process.txt`、`prepare-actual.txt`、`copied-kit-check.txt`、`prepared-layout-inspection.json`、`stable-verification.json`。旧失败日志原样保留，不覆盖成通过结果。

本次没有执行 `--launch`；静态校验通过不等于原生启动/确认/退出恢复验收，更不是 P1/H1/M1 或生产采用通过。

## 审查收敛

- 已修复固定 Host 可能选择 Vite renderer 的旁路，要求四个完整运行时树（包括 Python base），保留 T8/T9 测试身份边界。
- 最终审查发现仅凭产物标记不能排除旧构建：已增加显式无启动构建回执，核对构建前后输入及当前输出。复制编译产物时绑定初始回执的 hash/长度，发布前再次核对回执与当前状态，补齐并发更新窗口回归；这不是数字签名或可复现构建证明。
- 最终自查补充同步 stdin 失败的私有身份脱敏回归，事件与同步失败两条路径均已验证，不将私有 nonce 拼入异常信息。

## 保留的门槛

1. 完整便携 Node 的准备及真实夹具 `--check` 已于本次续办完成；后续仍须保留所有绑定运行时目录不变，漂移时重新拒绝。
2. 启动固定 Electron、空隔离 sidecar 及观察原生确认/退出恢复，需要单独真实运行授权。本轮没有启动。
3. 真实稳定仓库采用、当前稳定入口替换、正式发行激活、唯一数据写租约/迁移不在 E0 中启用。
4. P1/H1 原演练与 M1 继续保留各自授权条件；M1 还需安全凭据来源、provider adapter、provider/model 和费用预算，不能用 mock 填成验收通过。

本轮未安装依赖、未跑全量测试、未对真实仓库暂存/提交/stash/merge/push。测试中的 Git 写入只针对新建 disposable 仓库。

## 汇总与原始证据

- 选定 Node suites 合计 **94 passed、0 failed、1 skipped**；Python suites 合计 **33 passed、0 failed、2 skipped**。另列的 8 项旧回归 setup errors 不纳入这些通过数，不声称全量通过。
- `fixed-control-final.txt`、`adoption-regression.txt`、`python-fixed-final.txt`、`python-evolution-compat-final.txt`：最终定向结果。
- `python-layout-regression-final.txt`：包含 8 项旧 fixture 建链错误的原始失败输出。
- `fixed-control-build-final.txt`、`build-receipt-current.txt`：真实构建退出 0 及回执匹配。
- `prepare-final.txt`、`preparation-blocked.json`：真实准备退出 1、无新 run、未启动。
- `stable-verification.json`：稳定根 981 tracked 文件及 Git 状态未变。

所有上述证据文件位于 `F:/my-agent-main/.worktrees/sandbox/artifacts/fixed-control-e0-20261024/`。本次续办已完成运行时准备和真实隔离包静态校验，当前停止在单独授权的原生启动边界；不是把剩余编码转交给用户，也没有用手工合入绕过双版本机制。
