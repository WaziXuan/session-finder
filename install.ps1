# session-finder installer (Windows / PowerShell)
#
#   .\install.ps1                         # this PC only: skill + venv, nothing runs in the background
#   .\install.ps1 -Sync                   # also sync sessions across PCs via OneDrive\SessionSync
#   .\install.ps1 -Sync -SyncRoot D:\Dropbox\SessionSync   # any folder your sync client shares
#
# Rerunning it keeps an existing sync setup (the saved folder or SESSION_SYNC_ROOT).
param(
    [switch]$Sync,
    [string]$SyncRoot
)
$ErrorActionPreference = 'Stop'
# The script prints UTF-8; Windows PowerShell would read it in the ANSI code page and
# garble non-ASCII paths.
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false

$here = Split-Path -Parent $MyInvocation.MyCommand.Path

# The script and venv go into one client's skills folder: where an earlier install
# already put them, else WorkBuddy, Claude Code, Codex, CodeBuddy - the first one
# installed. The other clients get only SKILL.md, pointing there.
$clients = @('.workbuddy', '.claude', '.codex', '.codebuddy')
$present = @($clients | Where-Object { Test-Path (Join-Path $env:USERPROFILE $_) })
if (-not $present) { throw 'No supported client found (WorkBuddy, Claude Code, Codex or CodeBuddy CLI): install one first.' }
$homeClient = $clients | Where-Object { Test-Path (Join-Path $env:USERPROFILE "$_\skills\session-finder\scripts\find_session.py") } |
    Select-Object -First 1
if (-not $homeClient) { $homeClient = $present[0] }
$dest = Join-Path $env:USERPROFILE "$homeClient\skills\session-finder"
$venv = Join-Path $dest '.venv'
$fs = Join-Path $dest 'scripts\find_session.py'
$skill = (Get-Content (Join-Path $here 'session-finder\SKILL.md') -Raw -Encoding utf8).Replace(
    '.workbuddy/skills/session-finder', "$homeClient/skills/session-finder")
$utf8 = New-Object System.Text.UTF8Encoding $false

Write-Host "[1/4] Copying the skill to $dest"
New-Item -ItemType Directory -Force -Path (Join-Path $dest 'scripts') | Out-Null
Copy-Item (Join-Path $here 'session-finder\scripts\find_session.py') (Join-Path $dest 'scripts') -Force
[IO.File]::WriteAllText((Join-Path $dest 'SKILL.md'), $skill, $utf8)

foreach ($client in $present) {
    if ($client -eq $homeClient) { continue }
    $sk = Join-Path $env:USERPROFILE "$client\skills\session-finder"
    $extra = Get-ChildItem $sk -Force -ErrorAction SilentlyContinue | Where-Object Name -ne 'SKILL.md'
    if ($extra) {
        # An older standalone install (own scripts/.venv): move it aside, don't delete.
        $bak = Join-Path $env:LOCALAPPDATA ("session-finder\replaced\{0}-{1:yyyyMMdd-HHmmss}" -f $client.TrimStart('.'), (Get-Date))
        New-Item -ItemType Directory -Force -Path (Split-Path $bak) | Out-Null
        Move-Item $sk $bak
        Write-Host "      moved the old $sk to $bak"
    }
    New-Item -ItemType Directory -Force -Path $sk | Out-Null
    [IO.File]::WriteAllText((Join-Path $sk 'SKILL.md'), $skill, $utf8)
    Write-Host "      SKILL.md -> $sk"
}

Write-Host "[2/4] Creating a venv with zstandard (Codex compresses its sessions with zstd)"
$py = $null
foreach ($c in @('py', 'python', 'python3')) {
    $cmd = Get-Command $c -ErrorAction SilentlyContinue
    if ($cmd) { $py = $cmd.Source; break }
}
if (-not $py) { throw 'Python 3.9+ not found: https://www.python.org/downloads/' }
if (-not (Test-Path (Join-Path $venv 'Scripts\python.exe'))) {
    if ((Split-Path -Leaf $py) -eq 'py.exe') {
        & $py -3 -m venv $venv
    } else {
        & $py -m venv $venv
    }
}
$vpy = Join-Path $venv 'Scripts\python.exe'
& $vpy -m pip install --disable-pip-version-check -q zstandard

Write-Host "[3/4] Self-test"
$env:SESSION_FINDER_NO_UPDATE = '1'
& $vpy $fs list --limit 3

if ($Sync) {
    if (-not $SyncRoot) {
        $od = if ($env:OneDriveConsumer) { $env:OneDriveConsumer } else { $env:OneDrive }
        if (-not $od) { throw 'OneDrive not found: pass -SyncRoot <a folder your sync client shares between PCs>' }
        $SyncRoot = Join-Path $od 'SessionSync'
    }
    & $vpy $fs sync-root $SyncRoot | Out-Null
}
$root = & $vpy $fs sync-root 2>$null
Remove-Item Env:SESSION_FINDER_NO_UPDATE
if (-not $root) {
    Write-Host "[4/4] Multi-PC sync: off (search covers this PC only; rerun with -Sync to turn it on)"
} else {
    Write-Host "[4/4] Multi-PC sync to $root\$env:COMPUTERNAME (scheduled task SessionFinderSync)"
    # The task starts `watch`, which stays running (no time limit). The 5-minute repeat
    # only matters when it exited (crash, or a self-update restart): a second watcher
    # sees the lock and exits at once.
    $pyw = Join-Path $venv 'Scripts\pythonw.exe'
    Stop-ScheduledTask -TaskName 'SessionFinderSync' -ErrorAction SilentlyContinue
    # The venv launcher dies first; the real interpreter can linger holding watch.lock,
    # and a watcher started now would see the lock and quit. Wait for it to go.
    for ($i = 0; $i -lt 20; $i++) {
        $old = Get-CimInstance Win32_Process -Filter "name='pythonw.exe'" |
            Where-Object { $_.CommandLine -like '*find_session.py*watch*' }
        if (-not $old) { break }
        if ($i -eq 10) { $old | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } }
        Start-Sleep -Milliseconds 500
    }
    $action = New-ScheduledTaskAction -Execute $pyw -Argument ('"{0}" watch' -f $fs)
    $triggers = @(New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5))
    $triggers += New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
    $desc = 'session-finder: watch agent sessions and push changes to the SessionSync folder'
    try {
        Register-ScheduledTask -TaskName 'SessionFinderSync' -Action $action -Trigger $triggers -Settings $settings `
            -Description $desc -Force -ErrorAction Stop | Out-Null
    } catch {
        # A logon trigger can need admin rights; the 5-minute repeat alone still restarts it.
        Register-ScheduledTask -TaskName 'SessionFinderSync' -Action $action -Trigger $triggers[0] -Settings $settings `
            -Description $desc -Force | Out-Null
    }
    & $vpy $fs sync
    Start-ScheduledTask -TaskName 'SessionFinderSync'
    Write-Host "      log: $env:LOCALAPPDATA\session-finder\sync.log"
}

Write-Host ''
Write-Host 'Done. Start a new agent session so it picks up the skill.'
Write-Host "Try it: & '$vpy' '$fs' find <words from a session title>"
exit 0
