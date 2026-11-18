$global:oneClickScriptPath = Join-Path $PSScriptRoot 'prepare-one-click.ps1'

Describe 'prepare-one-click trusted path and repeat policy checks' {
    BeforeEach {
        $global:oneClickFixture = Join-Path ($TestDrive) ('one-click-test-' + [guid]::NewGuid().ToString('N'))
        $null = New-Item -ItemType Directory -Path (Join-Path $global:oneClickFixture '.worktrees/sandbox') -Force
        if (Test-Path -LiteralPath $global:oneClickScriptPath) { . $global:oneClickScriptPath -StableRoot $global:oneClickFixture }
    }

    It 'returns an actionable error for a non-Git root without creating a target' {
        $empty = Join-Path ($TestDrive) ('one-click-empty-' + [guid]::NewGuid().ToString('N'))
        $null = New-Item -ItemType Directory -Path $empty
        $output = @(& powershell.exe -NoProfile -NonInteractive -File $global:oneClickScriptPath -StableRoot $empty 2>&1)
        $code = $LASTEXITCODE
        $code | Should Be 1
        ($output -join "`n") | Should Match '(?m)^AUTO_SETUP_ERROR:.*sandbox'
        (Test-Path -LiteralPath (Join-Path (Split-Path $empty -Parent) 'my-agent-auto-target')) | Should Be $false
    }
    It 'resolves only the canonical sandbox and exact sibling target/policy paths' {
        $paths = Resolve-OneClickPaths -StableRoot $global:oneClickFixture
        $paths.SandboxRoot | Should Be ([IO.Path]::GetFullPath((Join-Path $global:oneClickFixture '.worktrees/sandbox')))
        $paths.TargetRoot | Should Be (Join-Path (Split-Path $global:oneClickFixture -Parent) 'my-agent-auto-target')
        $paths.PolicyPath | Should Be (Join-Path $paths.SandboxRoot 'data/self-evolution-auto.policy.json')
    }

    It 'rejects a relative stable root before touching prerequisites' {
        { Resolve-OneClickPaths -StableRoot '.\relative' } | Should Throw
    }

    It 'accepts an existing policy only when model, target, and image are exactly pinned' {
        $target = Join-Path (Split-Path $global:oneClickFixture -Parent) 'my-agent-auto-target'
        $image = 'sha256:' + ('a' * 64)
        $policy = [ordered]@{
            schema = 'self-evolution-auto.v1'; target_root = $target; model_id = '0x567-flash'; test_image = $image
            editable_files = @('agent-core/execution_lifecycle.py')
            verification_files = @('agent-core/execution_lifecycle.py', 'agent-core/tests/test_execution_lifecycle.py')
            tests = @('agent-core/tests/test_execution_lifecycle.py'); max_attempts = 3; test_timeout_sec = 90
        } | ConvertTo-Json -Depth 4 -Compress
        (Test-OneClickPolicyContent -Content $policy -ExpectedTarget $target -ExpectedImage $image).ModelId | Should Be '0x567-flash'
    }

    It 'rejects a policy that uses an alias instead of the exact registry ID' {
        $target = Join-Path (Split-Path $global:oneClickFixture -Parent) 'my-agent-auto-target'
        $policy = '{"schema":"self-evolution-auto.v1","target_root":"' + $target.Replace('\','\\') + '","model_id":"0x567","test_image":"sha256:' + ('a' * 64) + '","editable_files":["agent-core/execution_lifecycle.py"],"verification_files":["agent-core/execution_lifecycle.py","agent-core/tests/test_execution_lifecycle.py"],"tests":["agent-core/tests/test_execution_lifecycle.py"],"max_attempts":3,"test_timeout_sec":90}'
        { Test-OneClickPolicyContent -Content $policy -ExpectedTarget $target -ExpectedImage ('sha256:' + ('a' * 64)) } | Should Throw
    }
}
