#!/usr/bin/env bash
# Kavi Code - one-command installer for macOS / Linux.
#
#   curl -fsSL <raw-url>/scripts/install.sh | bash    # from the web
#   ./scripts/install.sh                              # from a checkout
#
# Prefers pipx (isolated, adds `kavi` to PATH). Falls back to a local .venv.
set -euo pipefail

REPO_URL="git+https://github.com/bahumukh/KaviCode.git"
TARGET=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  if [ -f "$DIR/pyproject.toml" ]; then
    TARGET="$DIR"
  fi
fi
if [ -z "$TARGET" ]; then
  TARGET="$REPO_URL"
fi

MIN_MAJOR=3
MIN_MINOR=11

say()  { printf '\033[32m[kavi]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[kavi]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31m[kavi]\033[0m %s\n' "$*" >&2; exit 1; }

# --- locate a suitable Python -------------------------------------------------
PY=""
for cand in python3 python; do
  if command -v "$cand" >/dev/null 2>&1; then
    if "$cand" -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= ($MIN_MAJOR,$MIN_MINOR) else 1)"; then
      PY="$cand"; break
    fi
  fi
done
[ -n "$PY" ] || die "Python ${MIN_MAJOR}.${MIN_MINOR}+ is required but was not found. Install it from https://www.python.org/downloads/ and re-run."
say "Using $("$PY" --version 2>&1) at $(command -v "$PY")"

# --- preferred path: pipx -----------------------------------------------------
if command -v pipx >/dev/null 2>&1; then
  say "Installing Kavi with pipx (isolated environment)..."
  pipx install --force "$TARGET"
  say "Installing Playwright browsers..."
  pipx run --spec "$TARGET" playwright install chromium
  pipx ensurepath >/dev/null 2>&1 || true
  say "Done. Open a new terminal and run:  kavi"
  exit 0
fi

# --- fallback: local virtual environment -------------------------------------
warn "pipx not found (recommended: '$PY -m pip install --user pipx'). Falling back to a project .venv."
VENV="$ROOT/.venv"
if [ ! -d "$VENV" ]; then
  say "Creating virtual environment at $VENV"
  "$PY" -m venv "$VENV"
fi
# shellcheck disable=SC1091
"$VENV/bin/python" -m pip install --upgrade pip >/dev/null
say "Installing Kavi and its dependencies..."
"$VENV/bin/python" -m pip install "$ROOT"
say "Installing Playwright browsers..."
"$VENV/bin/playwright" install chromium

say "Done. Run Kavi with either:"
say "    $VENV/bin/kavi"
say "    source $VENV/bin/activate && kavi"
