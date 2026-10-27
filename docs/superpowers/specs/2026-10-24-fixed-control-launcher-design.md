# 固定控制端与独立启动器：E0 设计

日期：2026-10-24。
状态：2026-10-24 用户已审阅并明确批准按本设计开始实现。本文件不是已实现报告、真实运行授权或稳定端发布授权。
实施位置：仅 `.worktrees/sandbox/`；不提交、合入、安装、替换当前稳定入口，不读取/复制凭据。

## 1. 目标和与已有设计的关系

用户正常使用两个业务版本：稳定版日常使用，实验版开发/试用；用户在产品内明确确认后由产品完成源码交付，不能每次依赖开发助手手工合入。

固定控制底座不是第三个业务版本。它是不会跟着稳定/实验源码热更新而消失的启动、确认、观察和恢复入口。此设计细化 2026-10-22 演化设计 4.3、12 节及契约附录 G，不代替正式 E 的运行采用、数据迁移和唯一写租约契约。

本次先做 **E0：可验证的固定入口、根目录绑定与故障阻断**。它解决安全执行源码合入的架构前提，不把源码合入标成新程序已生效。

## 2. 已核实的问题

- `desktop/vite.config.ts` 的 Electron 重建会先终止旧 Electron；当前 `start-desktop.bat` 最终进入该开发链。合入 main/preload/config 时，控制进程可能中途退出。
- `desktop/electron/sandbox-adopt.ts` 默认按模块所在目录推导稳定 Git 根。冻结以后，这个目录将是发行目录，不再是待合入的仓库。
- `desktop/electron/main.ts` 的 `AGENT_ROOT` 同时用于 Python 入口、state、锁和其它路径；Python `AgentPaths.from_root()` 又把 workspace/data/evolve 绑定在同一根下。
- T8/T9 已有固定包清单、可信 Python 环境指纹、独立 profile、sealed renderer 等机制，但它们是测试 Host，不是正式 Launcher。不得去掉测试身份就把它们当安装版。
- 现有源码采用服务已阻断 Vite Host 和测试 Host。缺少 Vite 环境变量不等于已证明运行的是固定可信发行；新的入口必须验证正向身份。

## 3. 方案选择

| 方案 | 得失 | 结论 |
| --- | --- | --- |
| 合入前暂时关 watch，仍从源码树运行 | 改动少；无法排除 lazy import、剩余 watcher、下一次启动的混版；控制与变更目标未隔离 | 不采用 |
| 固定 Launcher + 内容寻址的完整 Host/control 发行，显式绑定源码和数据 | 可复用现有冻结能力；不从被合入目录加载控制逻辑；可分步完成和验证 | 采用 |
| 本轮直接实现全部 E：安装、真实数据迁移、业务版本自动交接 | 最终完整，但涉及当前未实现的写租约、迁移、发行和真实运行授权 | 分后续切片，不顺带实施 |

## 4. 组件与根目录

```text
独立 Launcher（已审核、固定版本；普通源码合入不改写它）
  ├─ 验证发行清单、固定运行时、实例状态和目标绑定
  ├─ 直接启动固定 Electron，不启动 npm/Vite/watch
  └─ 固定 Host/control release
       ├─ 固定 main / preload / renderer / Python 可信模块
       ├─ 显式绑定的 stable_source_root → 稳定 Git worktree
       ├─ 显式绑定的 sandbox_source_root → experiment/sandbox worktree
       └─ 独立 state / profile / workspace（E0 只用新建隔离夹具）
```

目录职责：

- `launcher_root`：Launcher 自身及其固定 Node 运行时；普通采用不原位覆盖。
- `release_root/releases/<release_id>`：已发布只读语义代码包；不写 profile、缓存、数据、日志或 pycache，不从 stable/sandbox 动态 import。
- `launcher_state_root`：绑定、发行引用、实例状态和恢复记录；不在任一源码 worktree 中，不提供给候选写入。
- `host_state_root`、`host_profile_root`、`host_workspace_root`：明确属于该实例的可写位置，不因切换源码分支而改变。
- `stable_source_root`、`sandbox_source_root`：只作为 Git/实验工作目标，不当作可信代码加载目录或真实数据的隐式发现入口。

E0 的发行、状态和数据都放在 sandbox 专用 artifacts run-root，不能获得正式安装资格。正式部署按原设计另选经能力检查的 NTFS 私有位置；本轮不创建这些 workspace 外目录。

“固定”指完整清单、内容身份、受控加载和不原位更新，不宣称 hash 能阻止同用户恶意进程篡改。正式可信存储/访问控制另行验证。

## 5. 发行与目标绑定契约

### 5.1 固定发行

`release_id` 由规范化 manifest 的内容身份确定。清单登记：

- main/preload/renderer、Python 可信模块、必须的只读资源的精确路径、长度和 hash；拒绝缺失/额外可执行内容、链接及路径别名冲突。
- 构建输入身份、产物身份和验证证据；不可只用 Git commit 代替 dirty 输入快照，亦不可仅因有 source hash 就声称构建可复现。
- Electron distribution、Node 及 Python 运行环境的完整性依据。仅外部 Python 环境指纹仍属于开发夹具方案，漂移则拒绝；正式可分发运行时留待发布切片。
- Host/control 协议版本和所需数据布局版本。

采用实际字节复制，不假设 F: 支持 hardlink 去重；准备前估算所需空间，空间不足则停止。Node/Electron 所需的外部模块也必须进入精确发行清单或被固定运行时完整性覆盖，不从开发 node_modules 回退查找。

构建只生成新 stage，完整校验后发布为新内容目录；不覆盖当前或历史 release。构建不顺带激活、不安装依赖、不复制 data/.env/真实配置/会话/自演化可执行工具。

沿用已有控制包/Host 冻结算法；提取通用底层时必须保留 T8/T9 原有测试身份与限制，不直接把 `freezeHostFixture()` 改名成生产发行器。

### 5.2 安装绑定

绑定至少包含 `format`、`installation_id`、`mode`、`binding_revision`、两个 source 根、Git common-dir 身份、允许的稳定/实验分支、release/state/profile/workspace 根，以及固定运行时描述。严格 schema；无任意命令字符串、自由环境字典或凭据。

Host 通过 Launcher 自己持有的私有 stdin/stdio 引导接收绑定摘要、发行身份及本次 instance nonce，并校验磁盘描述和清单；启动握手返回同一身份。nonce 不进入 argv、环境、renderer 或日志，不构成同用户恶意进程隔离保证。

绑定由可信启动/安装端生成并审阅；renderer、候选页面、模型参数不能选根或用环境布尔量获得固定 Host 身份。Launcher 启动前及 Host 执行副作用前分别校验，确认绑定摘要和 revision 未变。

所有根使用规范绝对路径及实际文件系统身份，检查 junction/reparse/link、祖先关系和 Windows 大小写/尾空格点别名。stable/sandbox 必须为预期同仓库独立 worktree，且不与发行/控制状态目录重叠。

E0 使用 `launcher_fixture_v1` 独立命名空间；其中两个 source 根指专用 run-root 内另建的临时 Git 仓库/worktree，不是正在开发的真实 sandbox。发行、状态和两个源码夹具使用互不重叠的同级目录。只允许本轮创建并标记的临时 Git 仓库。真实仓库和真实 data 不允许作为夹具目标；原有 T8/T9 测试 Host 的真实采用禁令不变。

## 6. Host 接入与权限

- 新增窄的 Host runtime context，区分代码、数据、工作区和源码目标；默认开发启动保持旧行为和既有采用阻断。
- main 用已验证目标构造 `createSandboxAdopter({ stableRoot, sandboxRoot })`，不使用模块相对默认服务。状态检查和真正执行必须共享同一服务/上下文。
- 正向采用门槛要求：已验证固定 Host、已绑定目标、无恢复中事务、可信主窗口/frame/origin、main-owned 原生确认及确认后的快照复查。不能仅判断 `!VITE_DEV_SERVER_URL`。
- 固定工作台只加载其 sealed renderer；5174 候选预览不取得稳定 preload/采用权限。固定入口的“返回稳定”回到自身已核验 renderer，不导航到可变 5173。
- 不在 main.ts 继续堆大发行/根目录校验；拆成纯验证模块和可注入进程/文件系统边界，main 只负责组合及原生 UI。
- 需要 Python 时，从固定 Python 代码入口和明确可信解释器启动；私有结构化引导传入已验证布局。`AgentPaths` 新布局必须保持常规启动兼容，读写与路径格式化都遵守对应根；不能用 symlink 假装代码与数据已分离。
- 固定 control/worker 不从可变 evolve 目录加载代码。业务候选工具留在候选进程；需要新增可信控制代码时走单独控制发行评审/确认。
- E0 默认不发起模型、候选或真实 sidecar 运行；隔离 Host/sidecar 启动验收单独取得明确授权。缺凭据适配器不会被假模型填成通过。

## 7. 启动、退出和异常恢复

Launcher 直接用显式 argv、`shell:false` 启动固定 Electron。清除能改变代码加载、测试/开发模式或 Git 根的非批准环境项；凭据不写入 argv、日志、发行或候选环境。

启动状态：`verified → starting → ready → stopped`；无法验证、上次切换中断、已有未知实例或恢复未完成时进入 `maintenance_required`。`ready` 只表示固定 Host 握手成功，不代表候选/模型/正式数据验收通过。

- Launcher 保留并等待自己启动的进程句柄；不按进程名批量结束，不把 PID 存活视为租约合法。
- 同一绑定只允许一个受管实例。记录 installation/release/instance 身份、代次和不含秘密的状态；锁不自动偷取。跨端状态不一致时停止，不“再启动一份试试”。
- 固定 Host 提供与实例绑定的启动/退出握手；握手 timeout/进程异常作为失败记录，不自动反复重启。
- 采用进行中拒绝普通切换/退出，提示正在完成和保留恢复信息；不承诺能阻止 OS 强杀或断电。
- 首次 Git 写之前持久化操作身份、目标基线、确认摘要和阶段；精确备份 SHA 一旦得到即落盘；步骤结束及时写回回执。记录不得包含文件秘密内容。
- 崩溃恢复按事实读取 HEAD、index、工作树、精确 stash 和现有 merge 状态，仅展示已证实阶段。存在“Git 已执行、回执未落盘”的不确定窗口时进入维护，不自动重放 commit/stash/merge，也不删除本次或别人的备份。
- E0 的恢复保证是“不盲目继续、不丢现场、能诊断”，不是自动完成任意断电恢复；无法确定状态时需要用户处理。

实例互斥不等于真实数据唯一写租约。未完成正式写入交接适配器前，E0 不打开真实数据写入模式。

## 8. 用户实际使用与状态文案

首次发布完成后，用户从固定入口打开稳定工作台，在原壳预览位置试用实验版，验证后在可信稳定界面确认采用；不新增聊天面板，不要求用户手工操作 Git。

产品分别显示：

1. 实验结果及尚未完成的验收；
2. 源码交付（提交/合入/恢复/同步）；
3. 正在运行的发行身份；
4. 数据状态和恢复限制。

E0 源码合入成功只报告“源码已合入，当前程序仍运行发行 X”；不把清理 renderer URL 当作新版本已生效，不自动重启，也不把源码 sync 当作运行激活。

后续正式 E 切片必须根据实际合入树重新准备/核验发行、在数据副本验收，再按批准停机与唯一写租约交接。涉及控制底座更新时单独确认，Launcher 本体不在普通采用中自改。真实数据 schema/revision 或迁移状态不符时进入维护，不自动回旧快照丢弃新数据。

因此本轮完成 E0 后仍不能宣称“实验版一键变成日常稳定程序”的全链路已完成；该结论要等待发布/运行/数据各轴的真实验收。

## 9. 拟改范围与拆分

以下均位于 sandbox，实际实施前复核 dirty 文件并保留已有修改：

- `desktop/launcher/`：独立入口、绑定/发行校验、实例状态及受管启动；无模型、候选构建或通用 shell 执行器。
- `desktop/electron/fixed-host-context.ts`：严格 Host 布局解析与正向身份；不与 test-host 配置混用。
- `desktop/electron/main.ts`、`sandbox-adopt.ts`：组合显式目标、固定 renderer、采用生命周期/恢复回执；保持原生确认和既有安全检查。
- `desktop/scripts/`：显式 sandbox 构建/准备命令；不改真实 `start-desktop.bat`，不隐式安装或启动。
- `agent-core/paths.py` 及窄启动适配：只在固定 Host 隔离数据接入需要时增加结构化布局；不全局重写存储模块、不移真实数据。
- `desktop/tests/`、必要的 Python 定向测试：发行完整性、绑定、进程生命周期、恢复阻断及兼容回归。
- 使用/验收文档：注明 E0 夹具、未启用生产和正式切片剩余条件。

实现顺序：先纯发行/绑定验证，再 Launcher 状态与 fake child 端口测试，再 main/采用组合及恢复日志；最后隔离数据路径适配和获授权后的真实固定 Host 验收。不要为了提前点亮采用按钮先拆掉旧 gate。

## 10. 验收矩阵与停止条件

| 项目 | E0 验证方式 | 不代表 |
| --- | --- | --- |
| 内容固定 | 清单篡改/缺失/额外文件、链接、运行时漂移均拒绝；修改 source 不改已发布字节 | OS 级防篡改或正式发行通过 |
| 目标正确 | 真实临时 Git worktree 测试；错仓库/分支/根/旧 revision 拒绝；真实仓库绑定夹具拒绝 | 已允许合入当前真实仓库 |
| 身份门槛 | 缺 Vite 环境仍不能冒充固定 Host；测试 Host/预览 frame 不能采用 | renderer 按钮可见就是有权限 |
| 生命周期 | 注入 child 端口验证单实例、超时、退出、未知状态不重放；真实启动另列 | fake child 证明 Electron 存活 |
| 恢复 | Git 写入/回执边界故障注入，保留备份与现场，阻断未知状态 | 任意断电均可自动无损恢复 |
| 路径分离 | 空隔离数据读写、不写代码发行、不动态加载 source；普通 Python 路径定向兼容 | 真实数据迁移完成 |
| 真实体验 | 获授权后启动固定 Electron/空隔离 sidecar，观察原生确认、源码改变不重启控制端、退出/重开恢复 | HTTP 200、静态截图或注入 preload 已验收 |

仅运行相关定向测试和必要 TypeScript/desktop build，不跑全量测试、不装依赖；Python 检查先核实 `.venv` 的解释器和所需 import。缺依赖或环境能力就明确阻断。

稳定根在操作前后核对 HEAD、dirty 状态及已跟踪文件 hash；不暂存/提交/合入任何真实分支。P1/H1 原演练、M1 provider/model/预算以及首次稳定发布均保留各自授权和验收门槛。

## 11. 本轮范围外

- 启用/替换用户当前稳定启动入口、创建真实私有安装目录或捷径。
- 自动激活新业务发行、真实 data 写租约、迁移/逆迁移和真实数据回退。
- 复制/配置凭据、接通 provider、调用付费模型或运行原 P1/H1 候选。
- 把整个 dirty sandbox 自动视为可发布任务成果。
- 以设计、单元测试或旧 T9 证据宣称新 Launcher 已实现、已发布、已接受真实使用。

审阅点：是否按这个 E0 边界先实现固定入口和安全绑定，并在真实启用/程序换代/数据交接处保留明确门槛。