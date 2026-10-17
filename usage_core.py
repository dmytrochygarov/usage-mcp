"""
usage_core — shared logic for the Claude usage-limit cache.
============================================================
Stdlib only (urllib, json, os, time, datetime, subprocess). Imported by both entry points:
usage_cli.py (reader) and usage_mcp_server.py (reader).

One writer feeds a cache file; one read path (`get_usage`) serves it:

  writer  fetch_from_api()  pull — undocumented OAuth endpoint, throttled to
                                    <= 1 call / 180s (else persistent 429s).
  reader  get_usage()            — serve cache if fresh (<= STALE_SECONDS),
                                    else pull-on-stale, else serve stale cache;
                                    never raises. The cache exists so bursts of
                                    reads share one throttled pull.

Security: the OAuth access token is read from Claude Code's credential store (the
credentials file, or the login Keychain on macOS) only to build
the Authorization header. It is NEVER logged, printed, returned, or persisted.
There is deliberately no token-refresh logic — on 401 we degrade to cache and let
Claude Code's own refresh fix the token later.
"""

import os
import sys
import json
import time
import tempfile
import datetime
import subprocess
import urllib.request
import urllib.error

# ---------------------------------------------------------------------------
# Configuration (all env-overridable)
# ---------------------------------------------------------------------------
_HOME = os.path.expanduser("~")
# Claude Code honors CLAUDE_CONFIG_DIR for a relocated config dir; so do we.
_CLAUDE_DIR = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(_HOME, ".claude")

CACHE_PATH = os.environ.get("USAGE_CACHE_PATH", os.path.join(_CLAUDE_DIR, "usage-live.json"))
STATE_PATH = os.environ.get("USAGE_STATE_PATH", os.path.join(_CLAUDE_DIR, "usage-api-state.json"))
CREDENTIALS_PATH = os.environ.get("USAGE_CREDENTIALS_PATH", os.path.join(_CLAUDE_DIR, ".credentials.json"))

STALE_SECONDS = int(os.environ.get("USAGE_STALE_SECONDS", "120"))
API_MIN_INTERVAL = int(os.environ.get("USAGE_API_MIN_INTERVAL", "180"))
API_429_COOLDOWN = int(os.environ.get("USAGE_API_429_COOLDOWN", "900"))

FALLBACK_VERSION = "2.1.168"
OAUTH_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
OAUTH_BETA = "oauth-2025-04-20"
# macOS: Claude Code stores credentials in the login Keychain instead of a file.
KEYCHAIN_SERVICE = os.environ.get("USAGE_KEYCHAIN_SERVICE", "Claude Code-credentials")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _now() -> int:
    return int(time.time())


def _num(v):
    """Coerce a percentage-ish value to float, or None if it isn't numeric."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _iso_to_epoch(iso):
    """ISO-8601 (with 'Z' or offset) -> epoch seconds (int), or None."""
    if iso is None:
        return None
    if isinstance(iso, (int, float)):
        return int(iso)
    try:
        s = str(iso).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return int(dt.timestamp())
    except (ValueError, TypeError):
        return None


def _epoch_to_iso(epoch):
    if not epoch:
        return None
    try:
        return datetime.datetime.fromtimestamp(int(epoch), datetime.timezone.utc).isoformat()
    except (ValueError, OSError, TypeError):
        return None


def _humanize(seconds):
    """Seconds -> '1d 16h' / '3h 41m' / '12m' / 'now'. Negative clamps to 'now'.

    Shows the two largest non-zero units so the weekly (up to 7-day) window reads
    like Claude's own UI ('1d') rather than an unwieldy hour count ('160h')."""
    if seconds is None:
        return None
    seconds = int(seconds)
    if seconds <= 0:
        return "now"
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h" if hours else f"{days}d"
    if hours:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    if minutes:
        return f"{minutes}m"
    return "<1m"


# ---------------------------------------------------------------------------
# Atomic JSON read/write
# ---------------------------------------------------------------------------
def _atomic_write_json(path, data):
    """Write JSON atomically: temp file in the same dir, then os.replace."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".usage-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_cache(data):
    """Normalize a usage record to the canonical schema and atomically write it.

    fetch_from_api builds the dict; this fills in captured_at if absent and
    guarantees the window sub-objects exist.
    """
    def _window(w):
        w = w or {}
        return {
            "used_percentage": _num(w.get("used_percentage")),
            "resets_at": _iso_to_epoch(w.get("resets_at")),
        }

    def _scoped(items):
        out = []
        for it in (items or []):
            it = it or {}
            out.append({
                "model": it.get("model"),
                "used_percentage": _num(it.get("used_percentage")),
                "resets_at": _iso_to_epoch(it.get("resets_at")),
            })
        return out

    record = {
        "captured_at": int(data.get("captured_at") or _now()),
        "source": data.get("source", "unknown"),
        "five_hour": _window(data.get("five_hour")),
        "seven_day": _window(data.get("seven_day")),
        # Per-model weekly limits (e.g. "Weekly Fable"), from the API's limits[].
        "seven_day_scoped": _scoped(data.get("seven_day_scoped")),
        "claude_code_version": data.get("claude_code_version") or FALLBACK_VERSION,
    }
    _atomic_write_json(CACHE_PATH, record)
    return record


def load_cache():
    """Return the cached usage record, or None if absent/corrupt."""
    return _load_json(CACHE_PATH)


# ---------------------------------------------------------------------------
# API throttle state
# ---------------------------------------------------------------------------
def _load_state():
    return _load_json(STATE_PATH) or {}


def _record_call(now, is_429=False):
    state = _load_state()
    state["last_call_at"] = now
    if is_429:
        state["last_429_at"] = now
    try:
        _atomic_write_json(STATE_PATH, state)
    except OSError:
        pass  # throttle state is best-effort


def _throttle_allows(now):
    state = _load_state()
    if now - int(state.get("last_call_at", 0)) < API_MIN_INTERVAL:
        return False
    if now - int(state.get("last_429_at", 0)) < API_429_COOLDOWN:
        return False
    return True


# ---------------------------------------------------------------------------
# Writer — OAuth pull
# ---------------------------------------------------------------------------
def _keychain_credentials():
    """macOS only: the credentials JSON from the login Keychain, or None."""
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    try:
        return json.loads(out.stdout)
    except ValueError:
        return None


def _read_access_token():
    """Read claudeAiOauth.accessToken from Claude Code's credential store.

    Tries the credentials file first, then (on macOS) the login Keychain.
    The token is returned to the single caller (fetch_from_api) purely to build
    the Authorization header. It is never logged or persisted anywhere.
    """
    creds = _load_json(CREDENTIALS_PATH)
    if creds is None and sys.platform == "darwin":
        creds = _keychain_credentials()
    if creds is None:
        raise FileNotFoundError(f"credentials not readable at {CREDENTIALS_PATH}")
    token = (creds.get("claudeAiOauth") or {}).get("accessToken")
    if not token:
        raise RuntimeError("no claudeAiOauth.accessToken in credentials")
    return token


def _cached_version():
    cache = load_cache()
    if cache and cache.get("claude_code_version"):
        return cache["claude_code_version"]
    return FALLBACK_VERSION


def fetch_from_api():
    """Pull live usage from the OAuth endpoint, write the cache, return the record.

    Records the call in throttle state before firing so a burst of callers can't
    stampede the endpoint. On HTTP 429, additionally records last_429_at to open
    the longer cooldown window. Raises on any failure (caller degrades to cache).
    """
    token = _read_access_token()
    version = _cached_version()
    req = urllib.request.Request(
        OAUTH_USAGE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": OAUTH_BETA,
            "User-Agent": f"claude-code/{version}",
            "Accept": "application/json",
        },
        method="GET",
    )

    now = _now()
    _record_call(now)  # count the attempt regardless of outcome
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 429:
            _record_call(_now(), is_429=True)
        # Do not include response body — it could echo request material.
        raise RuntimeError(f"oauth usage endpoint returned HTTP {e.code}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"oauth usage endpoint unreachable: {e.reason}") from None

    five = payload.get("five_hour") or {}
    seven = payload.get("seven_day") or {}

    # Per-model weekly caps (what /usage labels "Weekly Fable", etc.) live in the
    # limits[] array as kind == "weekly_scoped"; the top-level seven_day_<model>
    # keys are null. Capture every scoped weekly entry, labeled by model name.
    scoped = []
    for lim in (payload.get("limits") or []):
        if lim.get("kind") == "weekly_scoped":
            model = ((lim.get("scope") or {}).get("model") or {}).get("display_name")
            scoped.append({
                "model": model,
                "used_percentage": _num(lim.get("percent")),
                "resets_at": _iso_to_epoch(lim.get("resets_at")),
            })

    record = {
        "captured_at": _now(),
        "source": "oauth-api",
        "five_hour": {
            "used_percentage": _num(five.get("utilization")),
            "resets_at": _iso_to_epoch(five.get("resets_at")),
        },
        "seven_day": {
            "used_percentage": _num(seven.get("utilization")),
            "resets_at": _iso_to_epoch(seven.get("resets_at")),
        },
        "seven_day_scoped": scoped,
        "claude_code_version": version,
    }
    return write_cache(record)


# ---------------------------------------------------------------------------
# The single read path
# ---------------------------------------------------------------------------
def _short_note(exc):
    """A one-line, token-free reason for degradation."""
    return f"live refresh failed ({type(exc).__name__}); serving cache"


def _serve(record, note=None):
    """Enrich a cache record with derived fields for presentation."""
    now = _now()
    captured = int(record.get("captured_at", 0))
    age = now - captured

    out = {
        "captured_at": captured,
        "captured_at_iso": _epoch_to_iso(captured),
        "source": record.get("source", "unknown"),
        "claude_code_version": record.get("claude_code_version"),
        "data_age_seconds": age,
        "stale": age > STALE_SECONDS,
    }
    def _enrich(w):
        w = dict(w or {})
        resets_at = w.get("resets_at")
        w["resets_at_iso"] = _epoch_to_iso(resets_at)
        w["resets_in"] = _humanize(resets_at - now) if resets_at else None
        return w

    for key in ("five_hour", "seven_day"):
        out[key] = _enrich(record.get(key))
    out["seven_day_scoped"] = [_enrich(s) for s in (record.get("seven_day_scoped") or [])]
    if note:
        out["note"] = note
    return out


def _no_data(note):
    return {"error": "no usage data available", "note": note}


def get_usage(force_refresh=False):
    """THE single read path used by both the CLI and the MCP tool.

    1. Fresh cache (age <= STALE_SECONDS) and not forced  -> serve it.
    2. Else, if the throttle allows                       -> pull live, serve fresh.
    3. On any pull failure                                -> serve stale cache + note.
    4. No cache at all and no live data                   -> structured error.

    Never raises; always returns a JSON-serializable dict.
    """
    cache = load_cache()
    now = _now()
    age = (now - int(cache.get("captured_at", 0))) if cache else None

    if not force_refresh and cache is not None and age is not None and age <= STALE_SECONDS:
        return _serve(cache)

    if _throttle_allows(now):
        try:
            fresh = fetch_from_api()
            return _serve(fresh)
        except Exception as exc:  # noqa: BLE001 — degrade on any failure
            note = _short_note(exc)
            if cache is not None:
                return _serve(cache, note=note)
            return _no_data(note)

    # Throttled and cache is stale (or forced): serve what we have.
    if cache is not None:
        return _serve(cache, note="live refresh throttled; serving cache")
    return _no_data("live refresh throttled and no cache available")
