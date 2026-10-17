"""
usage_cli.py — the one exposed CLI reader.
==========================================
Prints the current usage snapshot (from get_usage) as JSON to stdout.

    python usage_cli.py            # serve cache; refresh if stale & throttle allows
    python usage_cli.py --fresh    # force a live refresh (still throttle-respecting)

Exit 0 normally; exit 1 only when there is no data at all (no cache and no
reachable API).
"""

import sys
import json

import usage_core as core


def main(argv):
    force = "--fresh" in argv[1:]
    result = core.get_usage(force_refresh=force)
    sys.stdout.write(json.dumps(result, indent=2))
    sys.stdout.write("\n")
    return 1 if result.get("error") else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
