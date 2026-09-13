# Install ZILL on Windows: irm https://raw.githubusercontent.com/AkbarSheikh-debug/ZILL-Harness/main/install.ps1 | iex
# Uses uv if present, then pipx, then pip --user; with no Python 3.10+, it installs uv, which brings its own Python.
# Set $env:ZILL_SOURCE to install something else (e.g. zill-harness from PyPI, or a local path).
# No global ErrorActionPreference = 'Stop': Windows PowerShell 5.1 turns any stderr line from a
# native tool (uv prints notes there) into a fatal error. Run checks exit codes instead.

$source = if ($env:ZILL_SOURCE) { $env:ZILL_SOURCE } else { 'https://github.com/AkbarSheikh-debug/ZILL-Harness/archive/refs/heads/main.zip' }

function Say($msg) { Write-Host "zill: $msg" -ForegroundColor Cyan }
function Has($name) { [bool](Get-Command $name -ErrorAction SilentlyContinue) }
function Run { & $args[0] $args[1..($args.Length - 1)]; if ($LASTEXITCODE) { throw "$($args[0]) failed with exit code $LASTEXITCODE" } }

function Find-Python {
    foreach ($py in @('py', 'python', 'python3')) {
        if (Has $py) {
            # ssl too: a conda Python run outside its environment cannot load it, and ZILL needs HTTPS.
            & $py -c 'import sys, ssl; sys.exit(sys.version_info < (3, 10))' 2>$null
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

$py = Find-Python
if (Has 'uv') {
    Say 'installing with uv'
    # uv's own Python, not whatever is on PATH (an Anaconda base Python breaks HTTPS).
    Run uv tool install --force --managed-python $source
    uv tool update-shell *> $null
} elseif (Has 'pipx') {
    Say 'installing with pipx'
    Run pipx install --force $source
    pipx ensurepath *> $null
} elseif ($py) {
    Say "installing with $py -m pip --user"
    Run $py -m pip install --user --upgrade $source
    Add-UserPath (& $py -c "import sysconfig; print(sysconfig.get_path('scripts', 'nt_user'))")
} else {
    Say 'no Python 3.10+ found; installing uv (it brings its own Python)'
    Invoke-RestMethod https://astral.sh/uv/install.ps1 -ErrorAction Stop | Invoke-Expression
    $env:Path = "$HOME\.local\bin;$env:Path"
    Run uv tool install --force --managed-python --python 3.12 $source
    uv tool update-shell *> $null
}

Say 'done. Open a new terminal and run: zill'
