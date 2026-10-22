<#
.SYNOPSIS
    Host-side watchdog that keeps the gpu-ffmpeg container running.

.DESCRIPTION
    Docker's `restart: unless-stopped` policy only restarts a container when its
    process exits, and never restarts an unhealthy one. When the NVIDIA runtime
    prestart hook crashes while Docker creates the container (observed
    2026-10-22, see CLAUDE.md "GPU device loss") the container stays down until
    someone acts, and the worker's own GPU-loss self-heal cannot help because
    recovery needs a restart from outside the container.

    This script is that external recovery. Run it on a schedule (see
    docs/user/02_configuration.md) and it recreates gpu-ffmpeg whenever the
    container is missing, stopped, or unhealthy. It is idempotent and cheap:
    one `docker inspect` per run, and it only acts — and only writes to the log
    — when something is actually wrong. It always exits 0 so the scheduled task
    never reports a failure.

    Note: this will also revive a container an operator stopped by hand. If you
    want gpu-ffmpeg to stay stopped, disable the scheduled task first.
#>
[CmdletBinding()]
param(
    [string]$Service = "gpu-ffmpeg",
    [string]$ComposeFile,
    [string]$LogPath
)

# Native commands (docker) report failure through $LASTEXITCODE and write
# progress to stderr; with ErrorActionPreference=Stop PowerShell 5.1 would treat
# that stderr as a terminating error. Keep it non-terminating and check codes.
$ErrorActionPreference = "Continue"
$RepoRoot = Split-Path -Parent $PSScriptRoot
if (-not $ComposeFile) { $ComposeFile = Join-Path $RepoRoot "docker-compose.yml" }
if (-not $LogPath) { $LogPath = Join-Path $RepoRoot "data\gpu_watchdog.log" }

function Write-WatchdogLog([string]$Message) {
    $line = "{0} {1}" -f (Get-Date).ToString("o"), $Message
    try {
        $dir = Split-Path -Parent $LogPath
        if ($dir -and -not (Test-Path -LiteralPath $dir)) {
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
        }
        Add-Content -LiteralPath $LogPath -Value $line
    } catch {
        # A logging failure must never break the recovery attempt.
    }
}

# Resolve the container id from compose so a project/service rename cannot
# desync the watchdog from the real container name. Capture output before
# filtering: piping a native command straight into Select-Object -First can
# close the pipeline early and report a bogus non-zero exit code.
$psOutput = & docker compose -f $ComposeFile ps -a -q $Service 2>$null
$psExit = $LASTEXITCODE
$container = ($psOutput | Where-Object { $_ } | Select-Object -First 1)
if ($psExit -ne 0) {
    Write-WatchdogLog "docker compose ps failed (exit $psExit)"
    exit 0
}

$needsRecovery = $false
$reason = ""

if (-not $container) {
    $needsRecovery = $true
    $reason = "container not found"
} else {
    $state = & docker inspect -f "{{.State.Running}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}|{{.State.Error}}" $container 2>$null
    $inspectExit = $LASTEXITCODE
    $state = ($state | Where-Object { $_ } | Select-Object -First 1)
    if ($inspectExit -ne 0 -or -not $state) {
        $needsRecovery = $true
        $reason = "docker inspect failed"
    } else {
        $parts = $state.Split("|")
        $running = $parts[0]
        $health = $parts[1]
        $stateError = if ($parts.Length -ge 3) { $parts[2] } else { "" }
        if ($running -ne "true") {
            $needsRecovery = $true
            $reason = "not running"
        } elseif ($health -eq "unhealthy") {
            $needsRecovery = $true
            $reason = "unhealthy"
        } elseif ($stateError) {
            $needsRecovery = $true
            $reason = "state error: $stateError"
        }
    }
}

if (-not $needsRecovery) { exit 0 }

Write-WatchdogLog "recovering $Service ($reason); running docker compose up -d --force-recreate"
$output = & docker compose -f $ComposeFile up -d --force-recreate $Service 2>&1
$code = $LASTEXITCODE
Write-WatchdogLog ("recovery exit=$code output: " + ($output -join " "))
exit 0
