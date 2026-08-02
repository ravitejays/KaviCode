<#
  Kavi Code - one-command installer for Windows (PowerShell).

  Run from a checkout:
      powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1

  Prefers pipx (isolated, puts `kavi` on PATH). Falls back to a local .venv.
#>

$RepoUrl = "git+https://github.com/bahumukh/KaviCode.git"
$Target = $RepoUrl
if ($PSScriptRoot -and (Test-Path (Join-Path (Split-Path -Parent $PSScriptRoot) 'pyproject.toml'))) {
    $Target = Split-Path -Parent $PSScriptRoot
}
$MinMajor = 3
$MinMinor = 11

function Say  ($m) { Write-Host "[kavi] $m" -ForegroundColor Green }
function Warn ($m) { Write-Host "[kavi] $m" -ForegroundColor Yellow }
function Die  ($m) { Write-Host "[kavi] $m" -ForegroundColor Red; exit 1 }

# --- locate a suitable Python -------------------------------------------------
function Find-Python {
    $candidates = @()
    if (Get-Command py -ErrorAction SilentlyContinue) { $candidates += ,@('py', '-3') }
    if (Get-Command python -ErrorAction SilentlyContinue) { $candidates += ,@('python') }
    foreach ($c in $candidates) {
        $exe = $c[0]; $pre = @($c[1..($c.Length - 1)])
        $code = "import sys; raise SystemExit(0 if sys.version_info[:2] >= ($MinMajor,$MinMinor) else 1)"
        & $exe @pre -c $code 2>$null
        if ($LASTEXITCODE -eq 0) { return ,($c) }
    }
    return $null
}

$py = Find-Python
if (-not $py) {
    Die "Python $MinMajor.$MinMinor+ is required but was not found. Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH') and re-run."
}
$PyExe = $py[0]; $PyPre = @($py[1..($py.Length - 1)])
$ver = (& $PyExe @PyPre --version) 2>&1
Say "Using $ver"

# --- preferred path: pipx -----------------------------------------------------
if (Get-Command pipx -ErrorAction SilentlyContinue) {
    Say "Installing Kavi with pipx (isolated environment)..."
    pipx install --force "$Target"
    Say "Installing Playwright browsers..."
    pipx run --spec "$Target" playwright install chromium
    pipx ensurepath | Out-Null
    Say "Done. Open a NEW terminal and run:  kavi"
    exit 0
}

# --- fallback: local virtual environment -------------------------------------
Warn "pipx not found (recommended: '$PyExe -m pip install --user pipx'). Falling back to a project .venv."
$Venv = Join-Path $Root '.venv'
if (-not (Test-Path $Venv)) {
    Say "Creating virtual environment at $Venv"
    & $PyExe @PyPre -m venv "$Venv"
}
$VenvPy = Join-Path $Venv 'Scripts\python.exe'
& $VenvPy -m pip install --upgrade pip | Out-Null
Say "Installing Kavi and its dependencies..."
& $VenvPy -m pip install "$Root"
Say "Installing Playwright browsers..."
$VenvPlaywright = Join-Path $Venv 'Scripts\playwright.exe'
& $VenvPlaywright install chromium

Say "Done. Run Kavi with either:"
Say "    $(Join-Path $Venv 'Scripts\kavi.exe')"
Say "    $(Join-Path $Venv 'Scripts\Activate.ps1') ; kavi"
