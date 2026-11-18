param(
    [ValidateRange(1024, 65535)][int] $Port = 5175,
    [string] $PolicyPath,
    [switch] $Initialize,
    [ValidatePattern('^[A-Za-z0-9_./:@-]{1,128}$')][string] $ModelId
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath $PSScriptRoot).Path
if ((Split-Path $root -Leaf) -ne 'sandbox' -or (Split-Path (Split-Path $root -Parent) -Leaf) -ne '.worktrees') {
    throw 'This launcher can only run from the dedicated .worktrees/sandbox checkout.'
}
$defaultPolicy = Join-Path $root 'data\self-evolution-auto.policy.json'
if ($Initialize) {
    if ($PolicyPath -or -not $ModelId) {
        throw 'Initialize requires -ModelId <configured registry ID> and uses the standard policy path.'
    }
    if (Test-Path -LiteralPath $defaultPolicy) { throw 'An opt-in policy already exists; it was not overwritten.' }
    $repoParent = Split-Path (Split-Path (Split-Path $root -Parent) -Parent) -Parent
    $target = Join-Path $repoParent 'my-agent-auto-target'
    if (Test-Path -LiteralPath $target) { throw 'The experiment target already exists; inspect it before configuring a policy.' }
    $docker = (Get-Command docker.exe -CommandType Application -ErrorAction Stop).Source
    $git = (Get-Command git.exe -CommandType Application -ErrorAction Stop).Source
    $image = & $docker --host 'npipe:////./pipe/dockerDesktopLinuxEngine' image inspect `
        'my-agent-auto-verifier:pytest-8.3.5' --format '{{.Id}}'
    if ($LASTEXITCODE -or $image -notmatch '^sha256:[0-9a-f]{64}$') {
        throw 'Start Docker Desktop and load the verification image before initializing.'
    }
    & $git -c core.hooksPath=/dev/null clone --no-local --single-branch --branch experiment/sandbox -- $root $target
    if ($LASTEXITCODE) { throw 'Independent experiment clone failed.' }
    & $git -C $target -c core.hooksPath=/dev/null branch -m experiment-base
    if ($LASTEXITCODE) { throw 'The experiment-base branch could not be created.' }
    & $git -C $target remote remove origin
    if ($LASTEXITCODE) { throw 'The experiment clone source remote could not be removed.' }
    $settings = [ordered]@{
        schema = 'self-evolution-auto.v1'
        target_root = $target
        model_id = $ModelId
        test_image = [string]$image
        editable_files = @('agent-core/execution_lifecycle.py')
        verification_files = @('agent-core/execution_lifecycle.py', 'agent-core/tests/test_execution_lifecycle.py')
        tests = @('agent-core/tests/test_execution_lifecycle.py')
        max_attempts = 3
        test_timeout_sec = 90
    }
    $bytes = [System.Text.Encoding]::UTF8.GetBytes(($settings | ConvertTo-Json -Depth 4))
    $stream = [System.IO.File]::Open($defaultPolicy, 'CreateNew', 'Write', 'None')
    try { $stream.Write($bytes, 0, $bytes.Length) } finally { $stream.Dispose() }
}
if (-not $PolicyPath) { $PolicyPath = $defaultPolicy }
$policy = (Resolve-Path -LiteralPath $PolicyPath -ErrorAction Stop).Path
if (-not (Test-Path -LiteralPath $policy -PathType Leaf)) { throw 'The policy must be an existing JSON file.' }
$npm = (Get-Command npm.cmd -CommandType Application -ErrorAction Stop).Source
[byte[]] $token = New-Object byte[] 32
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
try { $rng.GetBytes($token) } finally { $rng.Dispose() }
$oldRoot = $env:MY_AGENT_ROOT
$oldPolicy = $env:MY_AGENT_SELF_EVOLUTION_AUTO_POLICY
$oldToken = $env:MY_AGENT_SELF_EVOLUTION_AUTO_TOKEN
$oldOrigins = $env:MY_AGENT_WS_ALLOWED_ORIGINS
try {
    $env:MY_AGENT_ROOT = $root
    $env:MY_AGENT_SELF_EVOLUTION_AUTO_POLICY = $policy
    $env:MY_AGENT_SELF_EVOLUTION_AUTO_TOKEN = [System.BitConverter]::ToString($token).Replace('-', '').ToLowerInvariant()
    $env:MY_AGENT_WS_ALLOWED_ORIGINS = "http://localhost:$Port,http://127.0.0.1:$Port"
    Push-Location -LiteralPath $root
    try {
        & $npm --prefix desktop run dev:main-only -- --port $Port --strictPort --host 127.0.0.1
        if ($LASTEXITCODE -ne 0) { throw "Sandbox desktop exited with code $LASTEXITCODE." }
    } finally {
        Pop-Location
    }
} finally {
    $env:MY_AGENT_ROOT = $oldRoot
    $env:MY_AGENT_SELF_EVOLUTION_AUTO_POLICY = $oldPolicy
    $env:MY_AGENT_SELF_EVOLUTION_AUTO_TOKEN = $oldToken
    $env:MY_AGENT_WS_ALLOWED_ORIGINS = $oldOrigins
}
