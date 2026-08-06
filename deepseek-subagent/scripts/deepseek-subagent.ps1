$ErrorActionPreference = "Stop"

function Resolve-DeepSeekPython {
    [CmdletBinding()]
    param(
        [string] $HomePath
    )

    $checked = New-Object 'System.Collections.Generic.List[string]'

    if (-not $HomePath) {
        if ($env:USERPROFILE) {
            $HomePath = $env:USERPROFILE
        }
        elseif ($HOME) {
            $HomePath = $HOME
        }
        else {
            $HomePath = [Environment]::GetFolderPath('UserProfile')
        }
    }

    if ($env:CODEX_PYTHON) {
        $configured = [Environment]::ExpandEnvironmentVariables($env:CODEX_PYTHON.Trim().Trim('"'))
        [void] $checked.Add("CODEX_PYTHON: $configured")
        if (Test-Path -LiteralPath $configured -PathType Leaf) {
            return [pscustomobject]@{
                Found = $true
                Executable = (Get-Item -LiteralPath $configured).FullName
                PrefixArgs = @()
                Source = 'CODEX_PYTHON'
                Checked = $checked.ToArray()
            }
        }

        $configuredCommand = Get-Command -Name $configured -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($configuredCommand) {
            return [pscustomobject]@{
                Found = $true
                Executable = $configuredCommand.Source
                PrefixArgs = @()
                Source = 'CODEX_PYTHON'
                Checked = $checked.ToArray()
            }
        }
    }

    $runtimeRoot = Join-Path $HomePath '.cache\codex-runtimes'
    $primaryPython = Join-Path $runtimeRoot 'codex-primary-runtime\dependencies\python\python.exe'
    [void] $checked.Add("Codex primary runtime: $primaryPython")
    if (Test-Path -LiteralPath $primaryPython -PathType Leaf) {
        return [pscustomobject]@{
            Found = $true
            Executable = (Get-Item -LiteralPath $primaryPython).FullName
            PrefixArgs = @()
            Source = 'codex-primary-runtime'
            Checked = $checked.ToArray()
        }
    }

    [void] $checked.Add("Other Codex runtimes: $runtimeRoot\*\dependencies\python\python.exe")
    if (Test-Path -LiteralPath $runtimeRoot -PathType Container) {
        $otherRuntimes = Get-ChildItem -LiteralPath $runtimeRoot -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -ne 'codex-primary-runtime' } |
            Sort-Object @{ Expression = { $_.LastWriteTimeUtc }; Descending = $true }, Name
        foreach ($runtime in $otherRuntimes) {
            $candidate = Join-Path $runtime.FullName 'dependencies\python\python.exe'
            if (Test-Path -LiteralPath $candidate -PathType Leaf) {
                return [pscustomobject]@{
                    Found = $true
                    Executable = (Get-Item -LiteralPath $candidate).FullName
                    PrefixArgs = @()
                    Source = "codex-runtime:$($runtime.Name)"
                    Checked = $checked.ToArray()
                }
            }
        }
    }

    [void] $checked.Add('PATH command: py -3')
    $pyCommand = Get-Command -Name 'py' -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($pyCommand) {
        & $pyCommand.Source -3 -c 'import sys' *> $null
        if ($LASTEXITCODE -eq 0) {
            return [pscustomobject]@{
                Found = $true
                Executable = $pyCommand.Source
                PrefixArgs = @('-3')
                Source = 'py -3'
                Checked = $checked.ToArray()
            }
        }
    }

    [void] $checked.Add('PATH command: python')
    $pythonCommand = Get-Command -Name 'python' -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($pythonCommand) {
        & $pythonCommand.Source -c 'import sys' *> $null
        if ($LASTEXITCODE -eq 0) {
            return [pscustomobject]@{
                Found = $true
                Executable = $pythonCommand.Source
                PrefixArgs = @()
                Source = 'python'
                Checked = $checked.ToArray()
            }
        }
    }

    return [pscustomobject]@{
        Found = $false
        Executable = $null
        PrefixArgs = @()
        Source = $null
        Checked = $checked.ToArray()
    }
}

if ($env:DEEPSEEK_SUBAGENT_LAUNCHER_IMPORT_ONLY -eq '1') {
    return
}

$selection = Resolve-DeepSeekPython
if (-not $selection.Found) {
    [Console]::Error.WriteLine('deepseek-subagent: no usable Python interpreter was found.')
    [Console]::Error.WriteLine('Checked, in priority order:')
    foreach ($location in $selection.Checked) {
        [Console]::Error.WriteLine("  - $location")
    }
    [Console]::Error.WriteLine('Set CODEX_PYTHON to a Python 3 executable or restore a Codex runtime.')
    exit 127
}

$managerPath = Join-Path $PSScriptRoot 'skill_manager.py'
if (-not (Test-Path -LiteralPath $managerPath -PathType Leaf)) {
    [Console]::Error.WriteLine("deepseek-subagent: manager entrypoint was not found: $managerPath")
    exit 2
}

$invokeArgs = @()
$invokeArgs += $selection.PrefixArgs
$invokeArgs += $managerPath
$invokeArgs += $args

& $selection.Executable @invokeArgs
$managerExitCode = $LASTEXITCODE
if ($null -eq $managerExitCode) {
    exit 1
}
exit [int] $managerExitCode
