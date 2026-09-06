[CmdletBinding()]
param(
    [switch]$Execute,
    [switch]$Uninstall,
    [string]$PythonwPath,
    [string]$StateDirectory = (Join-Path $env:USERPROFILE '.token-monitor-maintenance')
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$taskName = 'Token Monitor Upstream Check'
$owner = 'Token Monitor read-only upstream/quota check v1; no model, build, installation or app control.'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$observation = Join-Path $env:APPDATA 'Token Monitor\observations\token-monitor-quota.json'
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing -and $existing.Description -ne $owner) { throw 'Existing task is not owned by this installer.' }
# Packaged Codex redirects new LocalAppData writes into its package LocalCache. Task Scheduler
# runs outside that package, so keep the independent task runtime under the ordinary user profile.
if (-not [IO.Path]::IsPathRooted($StateDirectory)) { throw 'StateDirectory must be absolute.' }
$StateDirectory = [IO.Path]::GetFullPath($StateDirectory)
if ($StateDirectory.StartsWith(([IO.Path]::GetFullPath($env:LOCALAPPDATA).TrimEnd('\') + '\'), [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Use a directory outside LocalAppData to avoid packaged-app filesystem redirection.'
}
$runtime = Join-Path $StateDirectory 'runtime'
Write-Output "Task: $taskName; current user, Limited, at logon and every 6 hours; pythonw, no console."
Write-Output "Status: $StateDirectory; overlap: IgnoreNew; automatic retries: none; timeout: 5 minutes."
if (-not $Uninstall) {
    if (-not $PythonwPath) {
        $PythonwPath = Join-Path (Split-Path (Get-Command python.exe -ErrorAction Stop).Source) 'pythonw.exe'
    }
    if (-not (Test-Path -LiteralPath $PythonwPath -PathType Leaf) -or (Split-Path $PythonwPath -Leaf) -ne 'pythonw.exe') {
        throw 'An actual pythonw.exe interpreter is required.'
    }
    foreach ($name in @('check-upstream-task.py', 'notify-upstream-task.ps1')) {
        if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot $name) -PathType Leaf)) { throw "Missing source: $name" }
    }
}
if (-not $Execute) { Write-Output 'Preview only. Pass -Execute to apply.'; exit 0 }
if ($Uninstall) {
    if ($existing) { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false }
    Write-Output 'Task removed; status and evidence retained.'
    exit 0
}
if ($existing -and $existing.State -eq 'Running') { throw 'Task is running; installation deferred.' }
New-Item -ItemType Directory -Path $runtime -Force | Out-Null
# Snapshot the existing registration for exact local rollback. Keep one bounded previous copy.
if ($existing) {
    Export-ScheduledTask -TaskName $taskName | Set-Content -LiteralPath (Join-Path $StateDirectory 'task.previous.xml') -Encoding UTF8
}
foreach ($name in @('check-upstream-task.py', 'notify-upstream-task.ps1')) {
    $target = Join-Path $runtime $name
    if (Test-Path -LiteralPath $target) { Copy-Item -LiteralPath $target -Destination "$target.previous" -Force }
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination $target -Force
    if ((Get-FileHash -LiteralPath $target).Hash -ne (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot $name)).Hash) {
        throw "Installed script hash mismatch: $name"
    }
}
$script = Join-Path $runtime 'check-upstream-task.py'
$arguments = '"' + $script + '" --state-dir "' + $StateDirectory + '" --observation "' + $observation + '"'
# All paths are absolute. Leave Start in empty so Task Scheduler need not resolve a user
# profile working directory while constructing the interactive action's environment.
$action = New-ScheduledTaskAction -Execute $PythonwPath -Argument $arguments
$triggers = @(
    (New-ScheduledTaskTrigger -AtLogOn -User $identity),
    (New-ScheduledTaskTrigger -Once -At (Get-Date).AddHours(6) -RepetitionInterval (New-TimeSpan -Hours 6))
)
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $triggers -Principal $principal `
    -Settings $settings -Description $owner -Force | Out-Null
Export-ScheduledTask -TaskName $taskName | Set-Content -LiteralPath (Join-Path $StateDirectory 'task.xml') -Encoding UTF8
Write-Output 'Registered. Runtime copies are pinned; run this installer again to update them.'
