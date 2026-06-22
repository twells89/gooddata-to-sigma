#!/usr/bin/env python3
"""assess.py — GoodData estate migration-readiness readout (read-only).

STUB. Reuses discover.py's declarative export, then scores it against the
gooddata-to-sigma converter coverage. Full scoring tables land once live
discovery is validated and the converter's real coverage is known.

Usage:
  eval "$(../gooddata-to-sigma/scripts/get-token.sh)"
  python3 assess.py --workspace <id>     # or --all
"""
import argparse, json, os, sys, urllib.request, urllib.error

# MAQL constructs that drive conversion risk (see ../gooddata-to-sigma/refs/maql-mapping.md)
MAQL_RISK = ["BY ALL", "WITHIN", " BY ", "WHERE", "FOR PREVIOUS", "FOR NEXT", "TOP(", "BOTTOM("]
# insight types the converter handles cleanly today (see viz-type-mapping.md)
AUTO_VIZ = {"local:headline", "local:table", "local:column", "local:bar",
            "local:line", "local:area", "local:pie", "local:donut",
            "local:combo", "local:combo2", "local:scatter", "local:bubble"}
FLAG_VIZ = {"local:funnel", "local:pyramid", "local:sankey",
            "local:dependencywheel", "local:waterfall", "local:treemap", "local:repeater"}


def api(path):
    host = os.environ["GOODDATA_HOST"].rstrip("/")
    req = urllib.request.Request(host + path)
    req.add_header("Authorization", f"Bearer {os.environ['GOODDATA_TOKEN']}")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"GET {path} -> {e.code}: {e.read()[:300].decode(errors='replace')}")


def assess(ws):
    layout = api(f"/api/v1/layout/workspaces/{ws}?exclude=ACTIVITY_INFO")
    an = layout.get("analytics", {}) or {}
    metrics = an.get("metrics", []) or []
    insights = an.get("visualizationObjects", []) or []
    dashboards = an.get("analyticalDashboards", []) or []

    risk = {}
    for m in metrics:
        maql = ((m.get("content") or {}).get("maql") or "").upper()
        for k in MAQL_RISK:
            if k.strip() and k in maql:
                risk[k.strip()] = risk.get(k.strip(), 0) + 1

    viz = {}
    for i in insights:
        url = (i.get("content") or {}).get("visualizationUrl") or "?"
        viz[url] = viz.get(url, 0) + 1
    flagged = sum(v for k, v in viz.items() if k in FLAG_VIZ)

    print(f"=== {ws} ===")
    print(f"  metrics {len(metrics)} | insights {len(insights)} | dashboards {len(dashboards)}")
    print(f"  MAQL risk constructs: {risk or 'none'}")
    print(f"  insight types: {viz}")
    print(f"  flagged (non-auto) insights: {flagged}")
    print("  [STUB] per-dashboard AUTO/HINT/MANUAL/UNHANDLED scoring + shortlist TBD")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", default=os.environ.get("GOODDATA_WORKSPACE"))
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    if a.all:
        for w in (api("/api/v1/layout/workspaces").get("workspaces") or []):
            assess(w.get("id"))
    elif a.workspace:
        assess(a.workspace)
    else:
        sys.exit("--workspace <id> or --all required")


if __name__ == "__main__":
    main()
