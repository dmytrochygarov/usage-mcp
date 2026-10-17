<div align="center">

# 📊 usage-mcp

**Let Claude check your Claude usage limits — mid-conversation.**

The same 5-hour and weekly percentages that `/usage` shows, exposed as an MCP tool,
so you can just ask *"how much usage do I have left?"* and Claude looks it up itself.

![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)
![MCP](https://img.shields.io/badge/MCP-server-8A2BE2)
![Platforms](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)
![License](https://img.shields.io/badge/license-MIT-green)

</div>

---

## ✨ Why

`/usage` is great, but Claude itself can't see it. That means Claude can't pace a long
task, warn you before you hit a wall, or tell you whether there's room for one more big
refactor before the window resets.

With **usage-mcp** installed, Claude gets a `get_usage` tool and can answer things like:

> **You:** Do I have enough usage left to run the full test-suite migration?
>
> **Claude:** *(calls `get_usage`)* You're at **42%** of your 5-hour window (resets in
> **4h 8m**) and **15%** of your weekly limit. Plenty of room. Starting now.

It works in the **VS Code extension** too, where the terminal status line isn't rendered.

---

## 🚀 Install

### The easy way: let your agent do it

Clone the repo, open it in Claude Code (or any coding agent), and say:

```
Follow SETUP.md to install this for me.
```

[`SETUP.md`](SETUP.md) is written **for the agent**. It checks your Python, installs
the dependency, registers the MCP server with the right absolute paths for your OS,
and verifies everything end-to-end.

### The manual way

**Requirements:** Python 3.10+ and [Claude Code](https://claude.com/claude-code), logged
in with a Claude subscription (Pro / Max / Team). API-key-only logins have no usage limits to show.

```bash
# 1. Clone
git clone https://github.com/dmytrochygarov/usage-mcp.git
cd usage-mcp

# 2. Install the one dependency
python -m pip install -r requirements.txt

# 3. Sanity check: should print JSON with five_hour / seven_day percentages
python usage_cli.py

# 4. Register with Claude Code (user scope = available in every project)
#    Use ABSOLUTE paths for both the Python interpreter and the server script.
claude mcp add usage --scope user -- "$(python -c 'import sys;print(sys.executable)')" "$(pwd)/usage_mcp_server.py"
```

<details>
<summary><b>Windows (PowerShell) version of step 4</b></summary>

```powershell
$py = python -c "import sys; print(sys.executable)"
claude mcp add usage --scope user -- $py "$PWD\usage_mcp_server.py"
```

</details>

Then run `claude mcp list`. You should see `usage: ... ✔ Connected`. Start a fresh
session (or run `/mcp` to reconnect) and ask Claude about your usage.

---

## 🎯 Give Claude a usage budget

Paste this at the end of any prompt and change the numbers:

```text
Usage cap: 80% weekly. Check get_usage (usage MCP) now and every ~10 min, with a background timer while agents run. Stop starting new work 3 points under the cap. If the 5-hour window hits 90%, pause until it resets, then continue. Don't stop early to save usage.
```

For overnight runs with lots of subagents:

```text
Usage caps for this run: 80% weekly (every weekly bar; other sessions count), 90% 5-hour. Check get_usage (usage MCP) now, before each batch of agents, and every ~10 min with a background timer while agents run. Measure the burn rate and size the last batch to land under the cap; launch nothing within 3 points of it and give subagents the same stop line. At the 5-hour cap, pause until it resets, then continue. Don't stop early to save usage; finish with a fresh reading and remove your timers.
```

These lines come from about 25 real capped runs, many of them overnight. Each part
fixes a failure seen in those runs:

- **A concrete, labeled number.** With a number, Claude took a usage reading before
  starting every time. With no number, heavy phases went unchecked.
- **A timer while agents run.** Most failures came from long blind spots, not from
  reckless spending. While background agents worked, Claude often went 20+ minutes,
  sometimes hours, without checking. A timer keeps the checks going.
- **Stop 3 points early.** When the trigger was the cap itself ("stop at 99%"), runs
  ended at or past the line because work already in flight keeps spending. A small
  margin landed them just under.
- **Pause for the 5-hour reset.** Without this, a long run either quits early or hits
  the 5-hour wall mid-task. With it, Claude waits for the reset and carries on.
- **"Don't stop early."** This keeps Claude from quitting cautiously with most of the
  budget unused.

> ⚠️ This is a soft limit. Claude checks periodically, and usage is account-wide, so
> other sessions count too. Leave yourself some margin.

💡 **Running unattended?** Allow the tool so a permission prompt can't stall the run
overnight. Add `"mcp__usage__get_usage"` to `permissions.allow` in
`~/.claude/settings.json`.

---

## 🧠 How it works

```mermaid
flowchart LR
    C["Claude<br/>(calls get_usage)"] --> S["usage_mcp_server.py<br/>(FastMCP, stdio)"]
    S --> R{"cache fresh?<br/>(≤ 120 s)"}
    R -- yes --> J[("~/.claude/<br/>usage-live.json")]
    R -- no --> T{"throttle allows?<br/>(≤ 1 call / 180 s)"}
    T -- yes --> API["GET api.anthropic.com<br/>/api/oauth/usage"]
    API --> J
    T -- no --> J
```

1. Claude Code already stores your OAuth login: `~/.claude/.credentials.json` on
   Windows/Linux, and the **login Keychain** on macOS.
2. On a tool call, usage-mcp serves a small local cache if it's fresh. Otherwise it
   makes **one read-only GET** to the same usage endpoint `/usage` uses.
3. Calls are throttled (max 1 per 180 s, plus a 15-min backoff after any HTTP 429), so
   bursts of questions never hammer the endpoint.
4. If anything fails (offline, expired token, throttled), it **degrades to the last
   cached snapshot** with a note instead of erroring.

| File | Role |
|------|------|
| `usage_core.py` | All the logic. Stdlib only. |
| `usage_mcp_server.py` | MCP server: one tool, `get_usage(force_refresh=False)`. |
| `usage_cli.py` | Same data from the terminal: `python usage_cli.py [--fresh]`. |

### Example output

```json
{
  "source": "oauth-api",
  "data_age_seconds": 0,
  "stale": false,
  "five_hour":  { "used_percentage": 42.0, "resets_in": "4h 8m" },
  "seven_day":  { "used_percentage": 15.0, "resets_in": "1d 16h" },
  "seven_day_scoped": [
    { "model": "Fable", "used_percentage": 10.0, "resets_in": "6d 22h" }
  ]
}
```

`seven_day_scoped` lists per-model weekly caps (what `/usage` calls "Weekly Fable",
etc.). It's empty when your plan has none active.

---

## 🔒 Safety

- **Read-only.** It only ever performs a single `GET` on the usage endpoint.
- **Your token never leaves the request.** It's used solely for the `Authorization`
  header and is never logged, printed, returned to Claude, or written to the cache.
- **No token refresh logic.** On a 401 it just serves the cache and lets Claude Code
  refresh its own login.
- **Nothing sensitive in the repo.** The cache and throttle state live in `~/.claude/`.

---

## ⚙️ Configuration

All optional, via environment variables (pass them with `claude mcp add -e KEY=VALUE`):

| Variable | Default | Meaning |
|----------|---------|---------|
| `USAGE_STALE_SECONDS` | `120` | Cache older than this triggers a live pull. |
| `USAGE_API_MIN_INTERVAL` | `180` | Minimum seconds between live pulls. |
| `USAGE_API_429_COOLDOWN` | `900` | Backoff after an HTTP 429. |
| `CLAUDE_CONFIG_DIR` | `~/.claude` | Claude Code's config dir (respected if you've moved it). |
| `USAGE_CREDENTIALS_PATH` | `<config dir>/.credentials.json` | OAuth credentials file. |
| `USAGE_KEYCHAIN_SERVICE` | `Claude Code-credentials` | macOS Keychain item name. |
| `USAGE_CACHE_PATH` / `USAGE_STATE_PATH` | `<config dir>/usage-*.json` | Cache / throttle-state files. |

---

## 🩺 Troubleshooting

| Symptom | Fix |
|---------|-----|
| `credentials not readable` | Run `claude` and log in with your Claude account (`/login`). On macOS, allow the Keychain prompt the first time. |
| `HTTP 401` note | Your token expired. Use Claude Code normally for a moment; it refreshes itself. |
| `live refresh throttled; serving cache` | Expected. Wait ~3 minutes for fresh numbers. |
| `HTTP 429` note | Backed off for 15 min automatically. Nothing to do. |
| Tool not showing up in Claude | `claude mcp list`. Check both paths are absolute, then start a new session or `/mcp`. |
| `ModuleNotFoundError: mcp` | `mcp` was installed into a different Python than the one you registered. Re-run step 2 with that exact interpreter. |

---

## ⚠️ Disclaimer

This is an unofficial community tool, not affiliated with Anthropic. It relies on an
**undocumented** endpoint that Claude Code itself uses, which may change without notice.

## 📄 License

[MIT](LICENSE) © Dmytro Chygarov
