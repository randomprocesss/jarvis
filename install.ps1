# Install Jarvis for the person running this, on Windows. It checks what Jarvis needs, adds a `jarvis` command for this
# user, starts Jarvis and opens it in the browser. Everything after that is in the app (Setup).
#
# It needs no administrator rights, downloads nothing and changes nothing outside your data folder (%USERPROFILE%\.jarvis,
# or JARVIS_HOME) and one small file, %USERPROFILE%\.jarvis-bin\jarvis.cmd.
#
#     powershell -ExecutionPolicy Bypass -File install.ps1             install and start
#     powershell -ExecutionPolicy Bypass -File install.ps1 -NoStart    install only
#
# Not yet tried on a Windows computer: if a step fails, `python -m jarvis doctor` in this folder says what is missing.
param([switch]$NoStart)
$ErrorActionPreference = 'Stop'
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path

$Py = $null
foreach ($c in @('py -3', 'python', 'python3')) {
    $parts = $c.Split(' ')
    try {
        & $parts[0] $parts[1..($parts.Length - 1)] -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) { $Py = $c; break }
    } catch { }
}
if (-not $Py) {
    Write-Host 'Jarvis needs Python 3.9 or newer, and none was found. Install it from python.org (tick "Add python.exe to PATH"), then run this again.'
    exit 1
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host 'Note: git was not found. Jarvis runs without it, but it cannot update itself or read your repositories.'
}

$Bin = Join-Path $env:USERPROFILE '.jarvis-bin'
New-Item -ItemType Directory -Force -Path $Bin | Out-Null
$Launcher = Join-Path $Bin 'jarvis.cmd'
Set-Content -Path $Launcher -Encoding ASCII -Value "@echo off`r`ncd /d `"$Here`"`r`n$Py -m jarvis %*`r`n"
Write-Host "The jarvis command is at $Launcher"
if (($env:Path -split ';') -notcontains $Bin) {
    Write-Host "That folder is not on your PATH. Run Jarvis as: $Launcher   (or add $Bin to your PATH)"
}

Set-Location $Here
$parts = $Py.Split(' ')
& $parts[0] $parts[1..($parts.Length - 1)] -m jarvis doctor
if ($LASTEXITCODE -ne 0) { Write-Host 'Some things above need fixing. Jarvis starts anyway; Setup in the app shows the same list.' }
if (-not $NoStart) {
    & $parts[0] $parts[1..($parts.Length - 1)] -m jarvis start
    Write-Host 'Jarvis opens on its Setup page. To look around on made-up data first: jarvis demo'
}
