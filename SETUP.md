# SETUP.md: install instructions for an AI coding agent

> **Human?** You don't need to read this. Open this folder in Claude Code (or another
> coding agent) and say: **"Follow SETUP.md to install this for me."**
> For a human-readable overview, see [README.md](README.md).

---

## Instructions for the agent

You are installing **usage-mcp**, a local MCP server that lets Claude check the user's
Claude subscription usage limits (5-hour and weekly) mid-conversation. Do every step
below, in order. Run the commands yourself and don't ask the user to. Stop and report
clearly if a step fails in a way you can't fix.

**Ground rules**
- Never print, log, echo, or display the contents of `~/.claude/.credentials.json`
  or any OAuth token. You only ever need to check that the file *exists*.
- Use **absolute paths** everywhere in the MCP registration. Relative paths break,
  because Claude Code launches the server from another working directory.
- Detect the OS first and use the matching shell syntax (PowerShell on Windows,
  POSIX sh on macOS/Linux).
- Don't modify any file in this repo during setup.

### Step 1: Locate the repo

Get the absolute path of the directory containing this `SETUP.md`. Call it `REPO`.
Confirm `REPO/usage_mcp_server.py` exists.

### Step 2: Find a suitable Python (3.10+)

Try, in order, until one reports version ≥ 3.10:
- Windows: `py -3 --version`, `python --version`
- macOS/Linux: `python3 --version`, `python --version`

Get that interpreter's absolute path. Call it `PY`:

```
<interpreter> -c "import sys; print(sys.executable)"
```

If no Python ≥ 3.10 exists, stop and tell the user to install one (python.org, or
`brew install python` on macOS, or `winget install Python.Python.3.13` on Windows),
then re-run setup.

### Step 3: Install the dependency

```
"PY" -m pip install -r "REPO/requirements.txt"
```

If pip refuses with an "externally-managed-environment" error (common on Linux and
Homebrew Python), create a virtualenv instead and use its interpreter as `PY` from then on:

```
"PY" -m venv "REPO/.venv"
# macOS/Linux: PY = REPO/.venv/bin/python
# Windows:     PY = REPO\.venv\Scripts\python.exe
"PY" -m pip install -r "REPO/requirements.txt"
```

Verify: `"PY" -c "import mcp; print('mcp ok')"` must print `mcp ok`.

### Step 4: Check that Claude Code is logged in

- `claude --version` must succeed. If `claude` isn't on PATH, stop and tell the user
  to install Claude Code first.
- Credentials must exist (check existence only, never read them aloud):
  - Windows/Linux: `~/.claude/.credentials.json` (or `$CLAUDE_CONFIG_DIR/.credentials.json` if that env var is set)
  - macOS: Keychain item, checked with `security find-generic-password -s "Claude Code-credentials" > /dev/null && echo found`
    (the command without `-w` prints only metadata, not the secret)
- If neither exists, tell the user to run `claude` and log in with their Claude
  account via `/login`, then continue.

### Step 5: Smoke-test the core

```
"PY" "REPO/usage_cli.py" --fresh
```

Expected: JSON containing `five_hour.used_percentage` and `seven_day.used_percentage`
(numbers), and `"source": "oauth-api"`.

Interpreting other results:
- `"error": "no usage data available"` with a `credentials not readable` note: go back to Step 4.
- Note mentioning `HTTP 401`: token expired. Ask the user to use Claude Code briefly
  (it auto-refreshes), then retry.
- Note mentioning `throttled`: someone ran it in the last 3 minutes. That's fine, the
  cached data still proves it works.
- Any Python traceback: report it to the user verbatim.

### Step 6: Register the MCP server (user scope)

First check for an existing registration: `claude mcp list`. If an entry named
`usage` exists and points somewhere else, remove it with
`claude mcp remove usage --scope user`.

Then register, substituting the absolute paths:

```
claude mcp add usage --scope user -- "PY" "REPO/usage_mcp_server.py"
```

### Step 7: Verify the connection

Run `claude mcp list`. The `usage` entry must show **✔ Connected**.

If it shows as failed, run `"PY" "REPO/usage_mcp_server.py"` directly for ~3 seconds.
It should print `Starting usage MCP server...` to stderr and wait for input (that's
correct). Stop it, read any error it printed, fix, and re-check.

### Step 8: Report to the user

Tell the user, briefly:
1. Setup succeeded, plus their current numbers from Step 5 (e.g. "5-hour: 12%, resets in
   3h 20m · weekly: 30%").
2. They need to **start a new Claude Code session** (or run `/mcp` to reconnect) for
   the `get_usage` tool to appear.
3. After that they can ask things like *"how much usage do I have left?"*

---

## Uninstall (if the user asks)

```
claude mcp remove usage --scope user
```

Optionally delete the cache files `~/.claude/usage-live.json` and
`~/.claude/usage-api-state.json`, and the repo folder.
