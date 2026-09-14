# Install ZILL on Windows: irm https://raw.githubusercontent.com/AkbarSheikh-debug/ZILL-Harness/main/install.ps1 | iex
# Uses uv if present, then pipx, then pip --user; with no Python 3.10+, it installs uv, which brings its own Python.
# Set $env:ZILL_SOURCE to install something else (e.g. zill-harness from PyPI, or a local path).
# No global ErrorActionPreference = 'Stop': Windows PowerShell 5.1 turns any stderr line from a
# native tool (uv prints notes there) into a fatal error. Run checks exit codes instead.

$source = if ($env:ZILL_SOURCE) { $env:ZILL_SOURCE } else { 'https://github.com/AkbarSheikh-debug/ZILL-Harness/archive/refs/heads/main.zip' }
# The browser app (zill ui) comes along from ZILL_UI_SOURCE; set it to an empty string to skip the app.
$uiSource = if ($null -ne $env:ZILL_UI_SOURCE) { $env:ZILL_UI_SOURCE } else { 'https://github.com/AkbarSheikh-debug/ZILL-UI/archive/refs/heads/main.zip' }
$noUi = 'the app (zill ui) could not be installed; ZILL works without it (see the README to add it later)'

function Say($msg) { Write-Host "zill: $msg" -ForegroundColor Cyan }
function Has($name) { [bool](Get-Command $name -ErrorAction SilentlyContinue) }
function Run { & $args[0] $args[1..($args.Length - 1)]; if ($LASTEXITCODE) { throw "$($args[0]) failed with exit code $LASTEXITCODE" } }

function Find-Python {
    foreach ($py in @('py', 'python', 'python3')) {
        if (Has $py) {
            & $py -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>$null
            if ($LASTEXITCODE -eq 0) { return $py }
        }
    }
    return $null
}

function Add-UserPath($dir) {
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if (($userPath -split ';') -notcontains $dir) {
        [Environment]::SetEnvironmentVariable('Path', "$dir;$userPath", 'User')
        Say "added $dir to your PATH"
    }
}

# ZILL with the app when it installs, else ZILL alone. $extra holds uv's --python flag, if any.
function Install-Uv($extra) {
    if ($uiSource) {
        & uv tool install --force @extra $source --with $uiSource
        if ($LASTEXITCODE -eq 0) { return }
        Say $noUi
    }
    Run uv tool install --force @extra $source
}

$py = Find-Python
if (Has 'uv') {
    Say 'installing with uv'
    Install-Uv @()
    uv tool update-shell *> $null
} elseif (Has 'pipx') {
    Say 'installing with pipx'
    Run pipx install --force $source
    if ($uiSource) {
        & pipx inject --force zill-harness $uiSource
        if ($LASTEXITCODE) { Say $noUi }
    }
    pipx ensurepath *> $null
} elseif ($py) {
    Say "installing with $py -m pip --user"
    $installed = $false
    if ($uiSource) {
        & $py -m pip install --user --upgrade $source $uiSource
        $installed = $LASTEXITCODE -eq 0
        if (-not $installed) { Say $noUi }
    }
    if (-not $installed) { Run $py -m pip install --user --upgrade $source }
    Add-UserPath (& $py -c "import sysconfig; print(sysconfig.get_path('scripts', 'nt_user'))")
} else {
    Say 'no Python 3.10+ found; installing uv (it brings its own Python)'
    Invoke-RestMethod https://astral.sh/uv/install.ps1 -ErrorAction Stop | Invoke-Expression
    $env:Path = "$HOME\.local\bin;$env:Path"
    Install-Uv @('--python', '3.12')
    uv tool update-shell *> $null
}

Say 'done. Open a new terminal and run: zill    (or zill ui for the app)'
