# Windows 自我改造实验（默认关闭）

此入口只用于实验区，不替换稳定区或已打包应用。候选基线来自一次性实验仓库的**已提交 HEAD**，不包含工作树脏改动。Worker 在独立候选副本上改动文件；实验验证仅做 Python AST 语法检查，不运行候选代码、项目命令或测试。语法检查通过不是行为正确的证明。

本实验要求私有存储、候选目录及可应用改动的目标支持 Windows 文件 ACL（例如 NTFS）。如果项目留在 exFAT，请不要启用本页的 AST / 原生应用开关；改用[稳定桌面一键自动改造](./SELF-EVOLUTION-AUTO-EXFAT.md)，不需要迁移稳定仓库。

## 启动

1. 在开发检出的**同级目录**准备干净、独立、可丢弃的 Git clone（不是 Git worktree），且目标必须位于稳定仓库之外，包含已提交的 Python 文件（例如 `agent-core/` 下的源码）。建议在独立实验 clone 初始检出前设 `core.autocrlf=false`，让待应用文件的原始字节与提交基线一致。不要指向正在运行的应用、稳定树或它们的父子目录。需要复制未提交改动时，请先人工审阅并在一次性仓库中提交；实验入口不会代做。应用后留下的改动也不会自动提交；下次启动前请先在一次性仓库处理这些改动。
2. 在一次性仓库根目录放置 `.self-evolution-experimental-target.json`。`repository_root` 必须是仓库的规范绝对路径；不要在稳定仓库创建此标记。示例 PowerShell（先把示例路径替换为自己的**一次性仓库**）：

   ```powershell
   $target = (Resolve-Path -LiteralPath '..\my-agent-disposable').Path
   $marker = Join-Path $target '.self-evolution-experimental-target.json'
   if (Test-Path -LiteralPath $marker) { throw '请先核对已有的实验目标标记' }
   @{
     schema = 'self-evolution-experimental-target.v1'
     purpose = 'self_evolution_disposable_repository'
     repository_root = $target
   } | ConvertTo-Json -Compress | Set-Content -LiteralPath $marker -Encoding utf8NoBOM
   ```

3. 从有本实验代码及前后端依赖的开发检出运行 Electron；优先使用该检出的 `.venv`。仅在首次登记私有候选根时设置 `MY_AGENT_SELF_EVOLUTION_ENROLL_TRUST_ROOT=1`；后续启动去掉该变量。若运行环境提示根身份不符，停止调查，不要删除登记记录以重试。

   ```powershell
   $env:MY_AGENT_EXPERIMENTAL_SELF_EVOLUTION_STATIC_AST = '1'
   $env:MY_AGENT_SELF_EVOLUTION_EXPERIMENT_TARGET_ROOT = $target
   $env:MY_AGENT_SELF_EVOLUTION_ENROLL_TRUST_ROOT = '1' # 仅第一次
   $env:MY_AGENT_SELF_EVOLUTION_NATIVE_APPROVAL = '1'  # 不设置时仅候选与审阅
   npm --prefix desktop run dev:main-only
   ```

   也可独立运行 `server.py --host 127.0.0.1 --port 0 --experimental-self-evolution-static-ast --self-evolution-disposable-target <绝对路径>`，初次加 `--self-evolution-enroll-trust-root`。没有 Electron 主进程提供的确认通道时不能应用改动；不要手填确认令牌。实验侧车使用独立回环端口。不要用实验检出替换稳定实例。

## 使用与验证边界

- 在原来的主会话使用「动手」，明确让它改造应用并说明验收点。沿用模型工具调用作语义路由，「只聊」不启动 worker。候选内容发往模型前须在同一连接逐次确认；用户可以拒绝，不支持「全部同意」。
- worker 完成检查后，在原会话请求查看差异，再确认当前会话可读取这次候选差异。只有完整披露、未截断的可用差异会出现在选择列表里，默认不勾选任何文件。
- 选择文件后，Electron 会再次展示实验目标、选中的文件和操作摘要。确认后只写入独立实验仓库工作树上的所选文件，不暂存、不提交、不推送、不写入稳定区。超时、不同意或绑定失效不会授权应用；如果结果报告部分写入，先核对目标文件再操作。
- 新增、非 Python 或不在当前源码改造范围内的文件并不能靠这条语法校验通道获得验证。现有会话审批、Skill 安装和生产 gate 不会被这个开关放宽。真实模型连通性和桌面全链路必须独立验收，不由组件测试结果推定。

## 受信 Worker 的 Skill 安装

`skill_install.py` 提供结构化创建/导入、校验、预览、批准后写入与注册发现。受信 Worker 在运行宿主注入专用批准能力后，可在任务的独立候选 `evolve/skills/` 中使用此工具。支持内联文件、GitHub 仓库内指定 Skill 目录及 HTTPS 上单一 `SKILL.md`；批准绑定预览内容，用户拒绝则不安装。已有 Skill 不覆盖，导入的脚本不自动执行，发现新 Skill 也不自动授予加载权或工具权限。

这不是普通聊天或一键 exFAT 窗口的默认安装入口，不会自动写到稳定区。部件接线与定向验证见 `self_evolution_host.py`、`self_evolution_bootstrap.py`、`tests/test_self_evolution_skill_install_worker.py`（均位于 `agent-core/`）。

## AST 路径的隔离验证

在 Windows 的临时独立仓库中，已验证实际侧车启动和 WebSocket 连接，并用可重复的模型结果验证实际候选快照、Python AST、差异审阅、Python ↔ Node 回环确认和所选文件写入。确认点击由测试回调提供；批准只写所选文件，拒绝不写任何文件；两次验证均未改变 Git HEAD 和索引。未用真实外部模型发请求，也未把测试回调当作正式桌面窗口的用户验收。
