# Installing & Running Kavi Code

This guide covers installing and running **Kavi Code** on **Windows** and **macOS**
(the same steps work on Linux). Both flows assume you already have the Kavi source
folder (the `KaviCode` directory).

---

## Prerequisite (both OSes)

- **Python 3.11+**. Check your version:

  ```bash
  python --version      # or: python3 --version
  ```

  If it's missing or older, install it from
  [python.org/downloads](https://www.python.org/downloads/).
  On **Windows**, tick **"Add python.exe to PATH"** in the installer.

---

## macOS (and Linux)

```bash
cd /path/to/KaviCode
./scripts/install.sh
```

That's it. The script uses [`pipx`](https://pipx.pypa.io/) if you have it (isolated
environment, and it puts `kavi` on your PATH); otherwise it creates a local `.venv`
and installs into it. Then run:

```bash
kavi
```

If you used the `.venv` fallback and `kavi` isn't found, use either:

```bash
.venv/bin/kavi 
# or
source .venv/bin/activate && kavi
```

---

## Windows (PowerShell)

Open **PowerShell**, then:

```powershell
cd C:\path\to\KaviCode
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
```

Then **open a new terminal** and run:

```powershell
kavi
```

If `kavi` isn't found (you used the `.venv` fallback instead of pipx), use either:

```powershell
.venv\Scripts\kavi.exe
# or
.venv\Scripts\Activate.ps1 ; kavi
```

> **Tip:** Use **Windows Terminal** (not the old `cmd.exe` console) for the best
> rendering of the TUI — box drawing, colors, and the input bar all look right there.

---

## What happens automatically

- All dependencies (including `ddgs` for web search) install with the package — no
  separate steps.
- On the **very first launch**, if anything is still missing, Kavi installs it for you
  before starting. (Disable with `KAVI_NO_BOOTSTRAP=1` if you manage deps yourself.)
- `python -m kavi` works as a fallback anywhere if the `kavi` command isn't on PATH yet.

---

## Manual alternative (any OS, no scripts)

```bash
pipx install .        # recommended: isolated, adds `kavi` to PATH
# or
pip install .         # into your current environment
```

For development (editable install with test/lint tools):

```bash
python -m venv .venv
# macOS/Linux:  source .venv/bin/activate
# Windows:      .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

---

## First run — add a provider key

Once it opens, set up a model from inside the app:

```
/apikey groq gsk_...     # or: anthropic / openai / openrouter / nvidia / ...
/model                   # pick a model that key unlocks
```

Then just type your request.

You can also set keys via environment variables before launching, e.g.:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=sk-...
# Ollama needs no key; just run `ollama serve`
```

---

## Verify your setup

Run `/doctor` inside Kavi anytime to confirm your Python version, platform, API keys,
and optional tools (`ripgrep`, `ddgs`) are detected.

Optional but recommended: install **`ripgrep`** (`rg`) for faster search — Kavi falls
back to a pure-Python scan when it isn't present.

---

## Common launch commands

```bash
kavi                    # start a new session
kavi --resume           # pick a past session to resume
kavi -p "fix the bug"   # one-shot prompt (headless)
kavi --profile research # start in a specific use-case profile
kavi --mode auto        # auto-accept edits (still confirm destructive actions)
kavi --mode plan        # read-only planning session
python -m kavi          # fallback launcher if `kavi` isn't on PATH
```
