"""
usage_mcp_server.py — MCP server exposing the account's Claude usage limits.
============================================================================
A local FastMCP server (registered at user scope) with a single tool that lets
Claude check the account's rate-limit consumption mid-conversation — the same
numbers `/usage` shows. Serves a throttled cache; on a stale cache it pulls live
from the OAuth usage endpoint.

The OAuth access token never passes through this process's output — get_usage
returns only percentages and reset times.
"""

import sys
import json
import logging

from mcp.server.fastmcp import FastMCP

import usage_core as core

# ---------------------------------------------------------------------------
# Logging — stderr only (stdout is the MCP transport)
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("usage-mcp")

mcp = FastMCP("usage")


@mcp.tool()
def get_usage(force_refresh: bool = False) -> str:
    """Check this Claude account's current rate-limit usage.

    Returns the same figures Claude Code's `/usage` shows: how much of the
    rolling **5-hour** window and the **7-day (weekly)** window has been
    consumed, plus when each window resets.

    Field guide (JSON):
      five_hour.used_percentage / seven_day.used_percentage
          0–100. Higher = closer to the limit; at 100 that window is exhausted
          and requests are throttled until it resets.
      five_hour.resets_in / seven_day.resets_in
          Human countdown to reset, e.g. "3h 41m".
      seven_day_scoped
          List of per-model weekly caps (what /usage labels "Weekly Fable",
          etc.), each: model, used_percentage, resets_at, resets_in. Empty if
          the account has no model-scoped weekly limits active.
      source        "oauth-api" (pulled live from the usage endpoint).
      stale         true if the data is older than the freshness window; the
                    numbers are then a recent-but-not-instant snapshot.
      data_age_seconds   how old the served snapshot is.

    Args:
        force_refresh: Request a live pull instead of the cached value. Still
            respects the endpoint's rate limit, so it may return cached data
            with a note if a refresh isn't allowed yet.
    """
    try:
        result = core.get_usage(force_refresh=force_refresh)
    except Exception as exc:  # noqa: BLE001 — never surface a traceback to the model
        logger.exception("get_usage failed")
        result = {"error": f"{type(exc).__name__}: {exc}"}
    return json.dumps(result, indent=2)


if __name__ == "__main__":
    logger.info("Starting usage MCP server...")
    mcp.run(transport="stdio")
