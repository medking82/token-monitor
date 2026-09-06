param([Parameter(Mandatory = $true)][string]$StatusPath)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$status = Get-Content -LiteralPath $StatusPath -Raw | ConvertFrom-Json
$message = if ($status.pending_update) {
    'New upstream commit ' + $status.pending_update.upstream_sha.Substring(0, 7) + '. Update is pending checks and review.'
} else {
    'Upstream or quota observation needs attention. See the local check status.'
}
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$escaped = [Security.SecurityElement]::Escape($message)
$xml.LoadXml('<toast><visual><binding template="ToastGeneric"><text>Token Monitor maintenance</text><text>' + $escaped + '</text></binding></visual><audio silent="true" /></toast>')
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
$toast.Tag = 'tm-upstream'
$toast.Group = 'tm-maintenance'
$toast.ExpirationTime = [DateTimeOffset]::Now.AddHours(6)
$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Microsoft.Windows.PowerShell')
if ($notifier.Setting.ToString() -eq 'DisabledForUser') { exit 3 }
if ($notifier.Setting.ToString() -ne 'Enabled') { exit 4 }
$notifier.Show($toast)
