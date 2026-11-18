[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $StableRoot
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

function ConvertTo-NormalPath([string] $Path) {
    $full = [IO.Path]::GetFullPath($Path)
    $root = [IO.Path]::GetPathRoot($full)
    if ($full.Length -gt $root.Length) { return $full.TrimEnd([char]92) }
    return $full
}

function Assert-NoReparsePath([string] $Path) {
    $full = ConvertTo-NormalPath $Path
    $volume = [IO.Path]::GetPathRoot($full)
    if (!$volume) { throw '路径必须位于本地磁盘。' }
    $current = $volume
    $relative = $full.Substring($volume.Length)
    foreach ($part in $relative.Split([char]92, [StringSplitOptions]::RemoveEmptyEntries)) {
        if (!$part) { continue }
        $current = Join-Path $current $part
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw '稳定区或 sandbox 路径包含联接/符号链接；请改用真实路径。'
            }
        }
    }
}

function Resolve-OneClickPaths([string] $StableRoot) {
    if (![IO.Path]::IsPathRooted($StableRoot)) { throw 'StableRoot 必须是绝对路径。' }
    $stable = ConvertTo-NormalPath $StableRoot
    if ($stable -cne $StableRoot.TrimEnd([char]92)) {
        throw 'StableRoot 必须是规范绝对路径（不能包含相对段或尾部分隔符）。'
    }
    if (!(Test-Path -LiteralPath $stable -PathType Container)) { throw '找不到稳定仓库目录；请检查 StableRoot。' }
    Assert-NoReparsePath $stable
    $resolvedStable = ConvertTo-NormalPath (Resolve-Path -LiteralPath $stable).ProviderPath
    if ($resolvedStable -ine $stable) { throw 'StableRoot 不是规范真实路径；请改用解析后的路径。' }
    $sandbox = ConvertTo-NormalPath (Join-Path $stable '.worktrees/sandbox')
    Assert-NoReparsePath $sandbox
    if ((Test-Path -LiteralPath $sandbox) -and !(Test-Path -LiteralPath $sandbox -PathType Container)) { throw 'sandbox 路径不是目录，不会覆盖。' }
    $parent = Split-Path -Parent $stable
    $target = ConvertTo-NormalPath (Join-Path $parent 'my-agent-auto-target')
    if ([IO.Path]::GetPathRoot($target) -ine [IO.Path]::GetPathRoot($sandbox)) {
        throw '稳定仓库、sandbox 与独立目标必须位于同一磁盘。'
    }
    return [pscustomobject]@{
        StableRoot = $stable
        SandboxRoot = $sandbox
        TargetRoot = $target
        PolicyPath = Join-Path $sandbox 'data/self-evolution-auto.policy.json'
        VerifierContext = Join-Path $stable 'tools/self-evolution-verifier'
    }
}

function Test-OneClickPolicyContent([string] $Content, [string] $ExpectedTarget, [string] $ExpectedImage) {
    if ([string]::IsNullOrWhiteSpace($Content) -or $Content.Length -gt 32768) { throw '启动策略为空或超过大小限制。' }
    try { $policy = ConvertFrom-Json -InputObject $Content -ErrorAction Stop }
    catch { throw '启动策略不是有效 JSON；请人工检查后再启动。' }
    $expectedFields = @('schema','target_root','model_id','test_image','editable_files','verification_files','tests','max_attempts','test_timeout_sec')
    $actualFields = @($policy.PSObject.Properties | ForEach-Object { $_.Name })
    if ($actualFields.Count -ne $expectedFields.Count -or @($actualFields | Where-Object { $_ -notin $expectedFields }).Count -gt 0 -or @($expectedFields | Where-Object { $_ -notin $actualFields }).Count -gt 0) {
        throw '启动策略字段不符合受信任配置；请勿覆盖，先人工检查。'
    }
    if ($policy.schema -cne 'self-evolution-auto.v1' -or $policy.model_id -cne '0x567-flash' -or $policy.target_root -cne $ExpectedTarget -or $policy.test_image -cne $ExpectedImage) {
        throw '已有启动策略的模型、目标或镜像与固定配置不一致；请人工核对，不会覆盖。'
    }
    if ([IO.Path]::GetFullPath([string]$policy.target_root) -cne $ExpectedTarget -or $ExpectedImage -notmatch '^sha256:[0-9a-f]{64}$') {
        throw '已有启动策略包含非规范目标路径或无效镜像 ID。'
    }
    if ($policy.max_attempts -ne 3 -or $policy.test_timeout_sec -ne 90 -or
        (@($policy.editable_files) -join '|') -cne 'agent-core/execution_lifecycle.py' -or
        (@($policy.tests) -join '|') -cne 'agent-core/tests/test_execution_lifecycle.py' -or
        (@($policy.verification_files) -join '|') -cne 'agent-core/execution_lifecycle.py|agent-core/tests/test_execution_lifecycle.py') {
        throw '已有启动策略的授权文件或测试范围不是可信默认档案；请人工核对，不会覆盖。'
    }
    return [pscustomobject]@{ ModelId = $policy.model_id; TargetRoot = $policy.target_root; TestImage = $policy.test_image }
}

function Invoke-NativeChecked([string] $Executable, [string[]] $Arguments, [string] $WorkingDirectory, [hashtable] $Environment = @{}) {
    $oldValues = @{}
    foreach ($key in $Environment.Keys) {
        $oldValues[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
        [Environment]::SetEnvironmentVariable($key, [string]$Environment[$key], 'Process')
    }
    try {
        Push-Location -LiteralPath $WorkingDirectory
        try {
            # Git clone and Docker builds can report progress on stderr. Only the native exit code decides success.
            $ErrorActionPreference = "Continue"
            $output = @(& $Executable @Arguments 2>&1); $code = $LASTEXITCODE
        }
        finally { Pop-Location }
    }
    finally {
        foreach ($key in $Environment.Keys) { [Environment]::SetEnvironmentVariable($key, $oldValues[$key], 'Process') }
    }
    if ($code -ne 0) { throw ("命令失败：{0}" -f [IO.Path]::GetFileName($Executable)) }
    return (($output | ForEach-Object { [string]$_ }) -join "`n").Trim()
}

function Get-RequiredCommand([string] $Name) {
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if (!$command) { throw ("缺少 {0} 命令；请安装或修复开发环境后重试。" -f $Name) }
    return $command.Source
}

function Assert-RequiredRepositoryFiles($Paths) {
    foreach ($relative in @('agent-core/server.py','agent-core/self_evolution_auto.py','agent-core/llm_models.py','agent-core/llm_secrets.py','evolve/_index.core.toml','tools/self-evolution-verifier/Dockerfile')) {
        if (!(Test-Path -LiteralPath (Join-Path $Paths.StableRoot $relative) -PathType Leaf)) {
            throw ("缺少必需的稳定版运行时文件：{0}" -f $relative)
        }
    }
    foreach ($relative in @('agent-core/server.py','desktop/package.json','evolve/_index.core.toml')) {
        if (!(Test-Path -LiteralPath (Join-Path $Paths.SandboxRoot $relative) -PathType Leaf)) {
            throw ("实验区缺少必需的项目文件：{0}" -f $relative)
        }
    }
    if (!(Test-Path -LiteralPath (Join-Path $Paths.SandboxRoot '.git') -PathType Leaf) -and !(Test-Path -LiteralPath (Join-Path $Paths.SandboxRoot '.git') -PathType Container)) {
        throw '开发 sandbox 不是 Git 工作树。'
    }
    $venvPython = Join-Path $Paths.SandboxRoot '.venv/Scripts/python.exe'
    if (!(Test-Path -LiteralPath $venvPython -PathType Leaf)) { $venvPython = Join-Path $Paths.StableRoot '.venv/Scripts/python.exe' }
    if (!(Test-Path -LiteralPath $venvPython -PathType Leaf)) { throw '缺少项目 Python 虚拟环境，请先修复稳定开发版的 .venv。' }
    Assert-NoReparsePath $venvPython
    $Paths | Add-Member -NotePropertyName Python -NotePropertyValue $venvPython -Force
}

function Assert-SandboxBranchAndPython($Paths, [string] $Git) {
    $repo = Invoke-NativeChecked $Git @('-C', $Paths.SandboxRoot, 'rev-parse', '--show-toplevel') $Paths.SandboxRoot
    if ((ConvertTo-NormalPath $repo) -ine $Paths.SandboxRoot) { throw 'sandbox Git 根目录与规范路径不一致。' }
    $branch = Invoke-NativeChecked $Git @('-C', $Paths.SandboxRoot, 'branch', '--show-current') $Paths.SandboxRoot
    if ($branch -cne 'experiment/sandbox') { throw 'sandbox 当前分支不是 experiment/sandbox；请切换到开发分支。' }
    $probe = "import json,sys,websockets,httpx,rich; from pathlib import Path; from paths import AgentPaths; from llm_models import get_registry; p=AgentPaths.from_root(Path(sys.argv[1])); e=get_registry(p).get('0x567-flash'); print(json.dumps({'model_id': e.id if e else '', 'configured': bool(e and e.resolve_api_key(p))}))"
    $envMap = @{ PYTHONPATH = (Join-Path $Paths.StableRoot 'agent-core') }
    try { $result = Invoke-NativeChecked $Paths.Python @('-B', '-c', $probe, $Paths.SandboxRoot) $Paths.SandboxRoot $envMap }
    catch { throw '实验区 Python 或模型注册表无法加载，请检查项目虚拟环境和实验区源码。' }
    try { $status = ConvertFrom-Json -InputObject $result -ErrorAction Stop } catch { throw 'sandbox Python 无法读取现有 ModelRegistry；请修复虚拟环境后重试。' }
    if ($status.model_id -cne '0x567-flash') { throw 'sandbox ModelRegistry 未注册精确 ID 0x567-flash；请在现有模型设置中配置。' }
    if (!$status.configured) { throw 'sandbox 尚未配置 0x567-flash 凭据；请在应用设置中配置后重试。' }
}

function Get-DockerImageId([string] $Docker, [string] $Tag, [string] $Root) {
    try { $id = Invoke-NativeChecked $Docker @('--context','desktop-linux','image','inspect',$Tag,'--format','{{.Id}}') $Root }
    catch { return $null }
    if ($id -notmatch '^sha256:[0-9a-f]{64}$') { return $null }
    return $id
}

function Assert-DockerLinuxContext([string] $Docker, [string] $WorkingDirectory) {
    $endpoint = Invoke-NativeChecked $Docker @('context','inspect','desktop-linux','--format','{{.Endpoints.docker.Host}}') $WorkingDirectory
    if ($endpoint -cne 'npipe:////./pipe/dockerDesktopLinuxEngine') { throw 'Docker desktop-linux 未固定到 Docker Desktop Linux npipe；请恢复该 context 后重试。' }
    $os = Invoke-NativeChecked $Docker @('--context','desktop-linux','info','--format','{{.OSType}}') $WorkingDirectory
    if ($os -cne 'linux') { throw 'Docker Desktop 当前不是 Linux engine；请切换到 Linux containers 后重试。' }
}

function Get-OrBuildTrustedImage($Paths, [string] $Docker) {
    $tag = 'my-agent-auto-verifier:pytest-8.3.5'
    $id = Get-DockerImageId $Docker $tag $Paths.StableRoot
    if ($id) { return $id }
    $context = $Paths.VerifierContext
    $expected = @('.dockerignore','Dockerfile')
    # The committed .dockerignore allows only Dockerfile; no scripts, tests or data enter the build context.
    foreach ($file in $expected) { Assert-NoReparsePath (Join-Path $context $file) }
    $ignore = [IO.File]::ReadAllText((Join-Path $context '.dockerignore')).Replace("`r`n", "`n").TrimEnd()
    if ($ignore -cne "**`n!Dockerfile") { throw 'Docker 构建上下文必须排除 Dockerfile 之外的所有文件。' }
    $git = Get-RequiredCommand 'git'
    foreach ($file in $expected) {
        $relative = 'tools/self-evolution-verifier/' + $file
        $committed = Invoke-NativeChecked $git @('-C', $Paths.StableRoot, 'rev-parse', ('HEAD:' + $relative)) $Paths.StableRoot
        $working = Invoke-NativeChecked $git @('-C', $Paths.StableRoot, 'hash-object', (Join-Path $context $file)) $Paths.StableRoot
        if ($committed -cne $working) { throw 'Docker 构建文件与稳定区已提交版本不一致；不会使用未审核上下文。' }
    }
    $null = Invoke-NativeChecked $Docker @('--context','desktop-linux','build','--tag',$tag,$context) $Paths.StableRoot
    $id = Get-DockerImageId $Docker $tag $Paths.StableRoot
    if (!$id) { throw '本地验证镜像构建/检查失败；请检查 Docker Desktop 后重试。' }
    return $id
}

function Assert-IndependentTarget([string] $Git, [string] $Target, [string] $WorkingDirectory) {
    if (!(Test-Path -LiteralPath $Target -PathType Container)) { throw '独立实验目标不存在。' }
    Assert-NoReparsePath $Target
    $top = Invoke-NativeChecked $Git @('-C',$Target,'rev-parse','--show-toplevel') $WorkingDirectory
    if ((ConvertTo-NormalPath $top) -ine $Target) { throw '独立实验目标 Git 根目录不正确。' }
    $branch = Invoke-NativeChecked $Git @('-C',$Target,'branch','--show-current') $WorkingDirectory
    if ($branch -cne 'experiment-base') { throw '独立实验目标基线分支不是 experiment-base；不会改动现有目录。' }
    $remotes = Invoke-NativeChecked $Git @('-C',$Target,'remote') $WorkingDirectory
    if ($remotes) { throw '独立实验目标仍配置了 Git remote；请人工检查，不会修改已有目录。' }
}

function New-IndependentTarget([string] $Git, $Paths) {
    if (Test-Path -LiteralPath $Paths.TargetRoot) { throw '目标目录已存在但尚无受信任启动策略；请人工核对，不会覆盖或接管。' }
    $temp = Join-Path (Split-Path -Parent $Paths.TargetRoot) ('my-agent-auto-target.bootstrap.' + [guid]::NewGuid().ToString('N'))
    $null = Invoke-NativeChecked $Git @('clone','--no-local','--single-branch','--branch','experiment/sandbox',$Paths.SandboxRoot,$temp) $Paths.StableRoot
    $null = Invoke-NativeChecked $Git @('-C',$temp,'branch','-m','experiment-base') $Paths.StableRoot
    $null = Invoke-NativeChecked $Git @('-C',$temp,'remote','remove','origin') $Paths.StableRoot
    Assert-IndependentTarget $Git $temp $Paths.StableRoot
    if (Test-Path -LiteralPath $Paths.TargetRoot) { throw '目标目录在初始化期间出现；已保留临时克隆，请人工核对。' }
    [IO.Directory]::Move($temp, $Paths.TargetRoot)
    Assert-IndependentTarget $Git $Paths.TargetRoot $Paths.StableRoot
}

function Write-OneClickPolicyExclusive($Paths, [string] $ImageId) {
    if (!(Test-Path -LiteralPath (Split-Path -Parent $Paths.PolicyPath) -PathType Container)) { throw 'sandbox data 目录不存在；请先启动/初始化开发环境。' }
    $payload = [ordered]@{
        schema = 'self-evolution-auto.v1'
        target_root = $Paths.TargetRoot
        model_id = '0x567-flash'
        test_image = $ImageId
        editable_files = @('agent-core/execution_lifecycle.py')
        verification_files = @('agent-core/execution_lifecycle.py','agent-core/tests/test_execution_lifecycle.py')
        tests = @('agent-core/tests/test_execution_lifecycle.py')
        max_attempts = 3
        test_timeout_sec = 90
    }
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes(($payload | ConvertTo-Json -Depth 4) + "`n")
    $stream = $null
    $created = $false
    try {
        $stream = [IO.File]::Open($Paths.PolicyPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        $created = $true
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
    }
    catch {
        if ($stream) { $stream.Dispose(); $stream = $null }
        if ($created -and (Test-Path -LiteralPath $Paths.PolicyPath -PathType Leaf)) { Remove-Item -LiteralPath $Paths.PolicyPath -Force -ErrorAction SilentlyContinue }
        throw '启动策略无法独占创建；已存在文件不会覆盖，请人工核对。'
    }
    finally { if ($stream) { $stream.Dispose() } }
}

function Initialize-OneClickSandbox($Paths, [string] $Git) {
    if (Test-Path -LiteralPath $Paths.SandboxRoot) { return }
    $branch = 'experiment/sandbox'
    $args = @('-c','core.hooksPath=NUL','worktree','add')
    & $Git -C $Paths.StableRoot show-ref --verify --quiet "refs/heads/$branch"
    if ($LASTEXITCODE -eq 0) {
        # Do not switch, reset, or merge an existing experimental branch.
        $args += @($Paths.SandboxRoot, $branch)
    } else {
        $args += @('-b',$branch,$Paths.SandboxRoot,'HEAD')
    }
    $null = Invoke-NativeChecked $Git $args $Paths.StableRoot
    Assert-NoReparsePath $Paths.SandboxRoot
}

function Invoke-OneClickProvisioning([string] $StableRoot) {
    $paths = Resolve-OneClickPaths -StableRoot $StableRoot
    $git = Get-RequiredCommand 'git'
    try { $top = Invoke-NativeChecked $git @('rev-parse','--show-toplevel') $paths.StableRoot }
    catch { throw '稳定目录不是 Git 仓库，无法准备独立 sandbox。' }
    if ((ConvertTo-NormalPath $top) -ine $paths.StableRoot) { throw '稳定目录不是 Git 仓库的根目录。' }
    $docker = Get-RequiredCommand 'docker'
    Initialize-OneClickSandbox $paths $git
    Assert-RequiredRepositoryFiles $paths
    Assert-NoReparsePath $paths.PolicyPath
    Assert-SandboxBranchAndPython $paths $git
    Assert-DockerLinuxContext $docker $paths.StableRoot

    $policyExists = Test-Path -LiteralPath $paths.PolicyPath
    if ($policyExists) {
        if (Test-Path -LiteralPath $paths.TargetRoot -PathType Container) {
            Assert-IndependentTarget $git $paths.TargetRoot $paths.StableRoot
        } else { throw '已有启动策略但独立目标缺失；请人工恢复/核对，不会重建或覆盖。' }
    } elseif (Test-Path -LiteralPath $paths.TargetRoot) {
        throw '独立目标已存在但启动策略缺失；请人工核对，不会接管或覆盖。'
    }

    $imageId = Get-OrBuildTrustedImage $paths $docker
    if ($policyExists) {
        $content = [IO.File]::ReadAllText($paths.PolicyPath, [Text.Encoding]::UTF8)
        $null = Test-OneClickPolicyContent -Content $content -ExpectedTarget $paths.TargetRoot -ExpectedImage $imageId
        Write-Output '已验证现有 0x567-flash 自动模式策略、独立目标和本地镜像；未改写用户文件。'
        return
    }

    $null = [IO.Directory]::CreateDirectory((Split-Path -Parent $paths.PolicyPath))
    New-IndependentTarget $git $paths
    Write-OneClickPolicyExclusive $paths $imageId
    Write-Output '已准备独立自我改造目标与 0x567-flash 启动策略；未调用模型 API。'
}

if ($MyInvocation.InvocationName -ne '.') {
    [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
    try {
        Invoke-OneClickProvisioning -StableRoot $StableRoot
        exit 0
    }
    catch {
        $message = [string]$_.Exception.Message
        if ([string]::IsNullOrWhiteSpace($message)) { $message = '准备失败；请检查稳定仓库、sandbox、Docker 和模型设置。' }
        Write-Output ('AUTO_SETUP_ERROR:' + $message)
        exit 1
    }
}
