# For multi-PC sync users: push this checkout to SessionSync\_install. Every PC that
# syncs picks it up on its next run (the watcher within 10 minutes) and updates itself.
# Bump VERSION in find_session.py first, or nothing updates.
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $env:USERPROFILE '.workbuddy\skills\session-finder\.venv\Scripts\python.exe'
$fs = Join-Path $here 'session-finder\scripts\find_session.py'

$code = Get-Content $fs -Raw -Encoding utf8
if (-not $code.TrimEnd().EndsWith('# end of find_session.py')) { throw 'the last line of find_session.py must be # end of find_session.py' }
$ver = [regex]::Match($code, '(?m)^VERSION = "([^"]+)"').Groups[1].Value
$root = & $py $fs sync-root 2>$null
if (-not $root) { throw 'multi-PC sync is off on this PC (find_session.py sync-root <folder>)' }
$inst = Join-Path $root '_install'
$old = Get-Content (Join-Path $inst 'session-finder\scripts\find_session.py') -Raw -Encoding utf8 -ErrorAction SilentlyContinue
$oldVer = if ($old) { [regex]::Match($old, '(?m)^VERSION = "([^"]+)"').Groups[1].Value } else { '' }
if ($oldVer -eq $ver -and $old -ne $code) { throw "VERSION is still $ver but the code changed: bump VERSION first" }

# SKILL.md before the script: a machine that sees the new script version copies SKILL.md along with it.
New-Item -ItemType Directory -Force -Path (Join-Path $inst 'session-finder\scripts') | Out-Null
Copy-Item (Join-Path $here 'README.md'), (Join-Path $here 'README.zh-CN.md'), (Join-Path $here 'install.ps1'), (Join-Path $here 'install.sh') $inst -Force
Copy-Item (Join-Path $here 'session-finder\SKILL.md') (Join-Path $inst 'session-finder') -Force
Copy-Item $fs (Join-Path $inst 'session-finder\scripts') -Force

Write-Host "Published $ver to $inst (was $(if ($oldVer) { $oldVer } else { 'empty' }))."
