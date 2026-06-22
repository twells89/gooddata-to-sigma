#!/usr/bin/env python3
"""convert.py — GoodData workspace layout → Sigma data model spec.

Maps the LDM (datasets/attributes/facts/references) to a Sigma data model and
translates MAQL metrics to Sigma metric formulas (via maql.py). Datasets become
warehouse-table elements on the SAME warehouse GoodData reads, so parity runs
same-warehouse. Dimension datasets are emitted before fact datasets (Sigma
requires dim-before-fact ordering). Metrics that don't translate cleanly are
recorded in the flags file (flag, never fake).

Usage:
  python3 convert.py --workspace gd_workspace.json --connection-id <uuid> \
      --db CSA --schema TJ --out dm_spec.json --flags flags.json
"""
import argparse, json, re, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from maql import translate


def sid(prefix, gid):
    return f"{prefix}_{re.sub(r'[^a-zA-Z0-9]', '_', gid)}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--connection-id", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--schema", required=True)
    ap.add_argument("--folder-id", required=True)
    ap.add_argument("--out", default="dm_spec.json")
    ap.add_argument("--flags", default="flags.json")
    a = ap.parse_args()

    layout = json.load(open(a.workspace))
    ldm = layout.get("ldm", {}) or {}
    an = layout.get("analytics", {}) or {}
    datasets = ldm.get("datasets", []) or []

    # symbol tables: GoodData object id -> Sigma display name (for MAQL translation)
    syms = {"fact": {}, "attribute": {}, "metric": {}}
    for d in datasets:
        for at in d.get("attributes", []):
            syms["attribute"][at["id"]] = at.get("title") or at["id"]
        for f in d.get("facts", []):
            syms["fact"][f["id"]] = f.get("title") or f["id"]
    for m in an.get("metrics", []):
        syms["metric"][m["id"]] = m.get("title") or m["id"]

    # dim-before-fact: datasets that are referenced (targets) first
    referenced = {r["identifier"]["id"] for d in datasets for r in d.get("references", [])}
    ordered = [d for d in datasets if d["id"] in referenced] + \
              [d for d in datasets if d["id"] not in referenced]

    elements, flags = [], []
    el_id = {d["id"]: sid("el", d["id"]) for d in datasets}
    # per-dataset column id maps (for relationship keys + FK columns)
    col_by_srccol = {d["id"]: {} for d in datasets}

    for d in ordered:
        table = d["dataSourceTableId"]["id"]
        cols = []
        def add_col(cid, name, srccol):
            cols.append({"id": cid, "name": name, "formula": f"[{table}/{srccol}]"})
            col_by_srccol[d["id"]][srccol] = cid
        for at in d.get("attributes", []):
            add_col(sid("c", at["id"]), at.get("title") or at["id"], at["sourceColumn"])
        for f in d.get("facts", []):
            add_col(sid("c", f["id"]), f.get("title") or f["id"], f["sourceColumn"])
        # ensure FK columns referenced by relationships exist on the fact side
        for r in d.get("references", []):
            for s in r.get("sources", []):
                if s["column"] not in col_by_srccol[d["id"]]:
                    add_col(sid("fk", s["column"]), s["column"].replace("_", " ").title(), s["column"])

        elements.append({
            "id": el_id[d["id"]], "name": table, "kind": "table",
            "source": {"kind": "warehouse-table", "connectionId": a.connection_id,
                       "path": [a.db, a.schema, table]},
            "columns": cols,
        })

    # relationships (on the fact/source element, pointing at the dim/target)
    for d in ordered:
        el = next(e for e in elements if e["id"] == el_id[d["id"]])
        rels = []
        for r in d.get("references", []):
            tgt = r["identifier"]["id"]
            keys = []
            for s in r.get("sources", []):
                tgt_col = s["target"]["id"]  # target attribute id -> its column
                keys.append({"sourceColumnId": col_by_srccol[d["id"]][s["column"]],
                             "targetColumnId": col_by_srccol[tgt][_attr_srccol(datasets, tgt, tgt_col)]})
            rels.append({"id": sid("rel", f"{d['id']}_{tgt}"), "name": el_id[tgt].upper(),
                         "targetElementId": el_id[tgt], "keys": keys})
        if rels:
            el["relationships"] = rels

    # metrics -> attach to the fact element (the last/non-referenced one)
    fact_el = next(e for e in elements if e["id"] == el_id[ordered[-1]["id"]])
    metrics = []
    for m in an.get("metrics", []):
        res = translate((m.get("content") or {}).get("maql", ""), syms)
        if res["ok"]:
            metrics.append({"id": sid("m", m["id"]), "name": m.get("title") or m["id"], "formula": res["formula"]})
        else:
            flags.append({"metric": m["id"], "title": m.get("title"),
                          "maql": (m.get("content") or {}).get("maql"), "reason": res["reason"]})
    if metrics:
        fact_el["metrics"] = metrics

    spec = {"name": layout.get("name") or "GoodData Migration", "schemaVersion": 1,
            "folderId": a.folder_id,
            "pages": [{"id": "p1", "name": "Model", "elements": elements}]}
    json.dump(spec, open(a.out, "w"), indent=2)
    json.dump(flags, open(a.flags, "w"), indent=2)
    print(f"DM spec -> {a.out}: {len(elements)} elements, "
          f"{sum(len(e.get('columns', [])) for e in elements)} cols, {len(metrics)} metrics translated")
    print(f"flags -> {a.flags}: {len(flags)} metric(s) flagged")
    for fl in flags:
        print(f"   FLAG {fl['metric']}: {fl['reason']}")


def _attr_srccol(datasets, ds_id, attr_id):
    d = next(x for x in datasets if x["id"] == ds_id)
    at = next(a for a in d.get("attributes", []) if a["id"] == attr_id)
    return at["sourceColumn"]


if __name__ == "__main__":
    main()
