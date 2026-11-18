# F 盘 exFAT：自我改造自动模式

这是普通桌面 **sandbox 开发版**的显式选项，默认关闭。一次设置模型、授权源码和测试方案后，「动手」里的明确自我改造请求不需要逐步确认。「只聊」和附带文件的消息不启动这条流程。稳定区、平时的 Git 检出和数据不需要搬走，也不需要挂载 NTFS 虚拟盘。即使代码已合入稳定分支，正常稳定启动也不会开启此模式；请从下面的 sandbox 启动。只支持自动提交到独立实验分支，不会自动合入稳定区。

## 一次性配置

下面以 `F:\my-agent-main\.worktrees\sandbox` 为启动检出。先确认开发环境的 `.venv`、`desktop/node_modules` 和 Docker Desktop 的 Linux engine 已可用。下面的镜像仅安装 Python 和 pytest；不要把项目或聊天数据放进镜像。若要测试更多模块，在可信镜像中提前安装它们依赖的包，再固定新的本地镜像 ID。实际改造时不会下载镜像、依赖或联网测试。

如果本机已有 `my-agent-auto-verifier:pytest-8.3.5`，可一次初始化并启动。真人试用和自动化验收的真实模型统一用 `0x567-flash`：

```powershell
Set-Location 'F:\my-agent-main\.worktrees\sandbox'
.\start-self-evolution-auto.ps1 -Initialize -ModelId 0x567-flash
```

默认档案仅允许改 `execution_lifecycle.py` 并验证对应测试，适合先体验。其他模块请调整策略的源码与测试列表。若需从零准备镜像或自定义档案，可手动执行下面步骤（不要在已经初始化后再次 clone）：

```powershell
Set-Location 'F:\my-agent-main\.worktrees\sandbox'
$root = (Get-Location).Path
$target = 'F:\my-agent-auto-target'
if (Test-Path -LiteralPath $target) { throw '目标已存在：请核对仓库，不要覆盖它。' }
git clone --no-local --single-branch --branch experiment/sandbox $root $target
if ($LASTEXITCODE) { throw '实验仓库创建失败' }
git -C $target branch -m experiment-base
if ($LASTEXITCODE) { throw '实验基线分支准备失败' }
# 自改进只使用本地 Git 对象；无需留下推送入口。
git -C $target remote remove origin
if ($LASTEXITCODE) { throw '不能移除实验仓库的来源远端' }

# 如本机还没有该验证镜像，先由操作者构建一次。这一步可能联网下载依赖。
docker --context desktop-linux build --tag my-agent-auto-verifier:pytest-8.3.5 '.\tools\self-evolution-verifier'
if ($LASTEXITCODE) { throw '构建验证镜像失败' }
$image = docker --context desktop-linux image inspect my-agent-auto-verifier:pytest-8.3.5 --format '{{.Id}}'
if ($LASTEXITCODE) { throw '找不到本地验证镜像' }
$policy = Join-Path $root 'data\self-evolution-auto.policy.json'
if (Test-Path -LiteralPath $policy) { throw '已存在启动策略：请直接编辑，不覆盖。' }
[ordered]@{
  schema = 'self-evolution-auto.v1'
  target_root = $target
  model_id = '0x567-flash'
  test_image = $image
  editable_files = @('agent-core/execution_lifecycle.py')
  verification_files = @('agent-core/execution_lifecycle.py', 'agent-core/tests/test_execution_lifecycle.py')
  tests = @('agent-core/tests/test_execution_lifecycle.py')
  max_attempts = 3
  test_timeout_sec = 90
} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $policy -Encoding utf8
```

确认 sandbox 已配置 `0x567-flash` 的凭据，不在策略文件中写 API key。如果以前已初始化，直接把现有策略的 `model_id` 改为 `0x567-flash` 并重启，不要重新 clone，也不要用 `0x567` 别名代替精确的注册 ID。启用后，动手消息会交给该模型判定用途；只有判为明确自改造时，才进一步发送 `editable_files` 指定的 Git 基线源码。没有发送本地附件、聊天文件、工作区脏改动或未授权文件。配置文件是启动方的长期授权，不由 LLM 生成或修改。

```powershell
Set-Location 'F:\my-agent-main\.worktrees\sandbox'
.\start-self-evolution-auto.ps1
```

策略文件会被保留；以后只运行这个启动器。它为本次启动随机生成连接令牌并使用独立的本地端口；端口占用时改用参数 `-Port 5176`，不要连到稳定实例。关闭自动模式时改用普通启动入口。不设置系统环境变量。

## 运行、验收和回退

在「动手」输入带明确范围与验收点的请求，例如「在本应用实验区调整执行生命周期的某个具体行为，保留原有停止语义」。LLM 判断意图、提出改动并审阅，程序自动运行基线与候选两套相同的定向测试；失败可有限返工。把模型改动约束为指定的**已跟踪 Python 源文件**，不能修改测试文件、任意运行宿主命令或推送。仅 Python/pytest 档案，暂不支持依赖插件的测试、前端运行验收或修改未授权文件。基线测试失败、镜像不可用、无测试通过或审阅未通过，都不会发布提交。

成功只推进独立仓库的 `experiment-auto` 分支引用；不覆盖 `experiment-base` 工作树，不把多个文件逐个写进稳定区。下一轮沿 `experiment-auto` 继续。跑以前或运行中不要签出/改动这个引用。查看：

```powershell
git -C 'F:\my-agent-auto-target' log --oneline experiment-base..experiment-auto
git -C 'F:\my-agent-auto-target' diff experiment-base...experiment-auto
```

回退时停止自动模式，在**另一个审阅分支**从 `experiment-auto` 创建回退提交再复核；不要强制重置用户工作树。需要采用到稳定区时，走现有实验→稳定流程；这里不会把 LLM 的语义判断当成生产合并授权。

验证容器使用预配置的本地 Linux 镜像，候选只经管道送入容器临时目录，不挂载 F 盘、宿主凭据或 Docker socket，不允许容器联网。现有 Docker 自身的镜像/虚拟机存储位置不由项目控制。exFAT 并不提供本地多用户文件隔离：这个模式只用于可信单机开发环境，不适用于多个不互信账户共同写目标盘的场景。

## 验收约定：真实模型只用 0x567

- 自我改造的语义路由、生成、失败返工、审阅，统一使用同一条 `0x567-flash` 配置；不走默认 Flash/Pro，也不切换其他供应商。凭据缺失、接口不可达或模型输出不合规，记录具体失败阶段，不用其他模型补过。
- 本地 Python、Docker、Git 与 WebSocket 的定向测试，以及固定响应的模型回放，不调用外部模型。分别记录真实模型验收与本地定向测试，不能用后者代替前者。日常无需跑全量测试。
- 真实验收只把合成测试请求或试用档案批准的 Git 源码发给 0x567，不发送稳定区脏改动、附件、聊天记录或密钥。

### 最短验收路径

1. 用上面命令以 `0x567-flash` 启动实验窗口。普通对话模型也先切到 `0x567-flash`：讨论会交回普通对话，不能让它沿用其他供应商。关掉本轮无关的自动识别等后台模型功能，再在「动手」发「聊聊执行生命周期为什么要区分取消与失败，先不改代码」，预期是正常回复、没有新实验提交。不要把该消息当成自我改造授权。
2. 再发「请现在改造本应用的实验仓库：在 `execution_lifecycle.py` 里给 `ExecutionLifecycle` 的说明补一句中文，明确这个类只维护执行状态，取消模型和关闭工具由调用方负责；不要改运行逻辑或测试。」预期：基线和候选的定向测试跑完，无逐步确认，返回 `experiment-auto` 的提交号。若测试或审阅失败，应报告原因且不推进分支。
3. 查 `experiment-base...experiment-auto` 的差异，确认只改了指定文件；`experiment-base` 的检出、暂存区和稳定区保持原样。记录模型注册 ID、是否弹确认、测试结果和提交号。真实 0x567 请求成功且产出符合目标，才记为通过。

## 本机验证记录

此前在 F 盘 exFAT 临时仓库验证了真实 Docker、Git 和已认证 WebSocket 的路径：匿名连接拒绝，模型给出修改和审阅后自动提交，零次逐步确认；独立测试验证候选行为，原工作树、HEAD 和索引未改动。固定模型响应的回放也验证了候选测试失败后的返工。那次真实模型是 DeepSeek Flash，仅作历史记录，**不计为本次 0x567 验收**。随后在合入候选上用 `0x567-flash` 做了单独实测：普通讨论语义判为聊天；明确的合成需求由同一模型生成并审阅修改，合入候选的已认证 WebSocket 中零次确认，真实 Docker 跑定向测试后自动提交到 F 盘临时实验分支。独立验收用例在改前失败、改后通过，检出、HEAD 和索引未变。这不是正式 Electron 窗口的验收。
