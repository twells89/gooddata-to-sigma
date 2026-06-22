#!/usr/bin/env python3
"""build_workbook.py — GoodData insights + dashboard → Sigma workbook spec.

Binds to the already-migrated Sigma data model (the DM convert.py produced).
A master table sources the DM fact element; KPIs and charts source the master.
Measures are resolved by recursively inlining the metric MAQL down to fact
aggregates (with the element prefix); a `view` attribute on a *related* dataset
is resolved to a cross-element reference [FACT/REL_NAME/Dim].

Usage:
  python3 build_workbook.py --workspace gd_workspace.json \
     --data-model-id <uuid> --fact-element <elId> --fact-name ORDER_FACT \
     --rel-name EL_CUSTOMER --fact-dataset order --folder-id <uuid> --out wb_spec.json
"""
import argparse, json, re, sys, os

AGG = {"SUM": "Sum", "AVG": "Avg", "MIN": "Min", "MAX": "Max", "MEDIAN": "Median"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--data-model-id", required=True)
    ap.add_argument("--fact-element", required=True)
    ap.add_argument("--fact-name", required=True)
    ap.add_argument("--rel-name", required=True)
    ap.add_argument("--fact-dataset", required=True)
    ap.add_argument("--folder-id", required=True)
    ap.add_argument("--out", default="wb_spec.json")
    a = ap.parse_args()
    P = a.fact_name  # element-name prefix for formulas

    layout = json.load(open(a.workspace)); ldm = layout["ldm"]; an = layout["analytics"]
    # symbol tables
    attr = {}; fact = {}; metric_maql = {}
    for d in ldm["datasets"]:
        for at in d.get("attributes", []): attr[at["id"]] = {"title": at["title"], "ds": d["id"]}
        for f in d.get("facts", []): fact[f["id"]] = {"title": f["title"], "ds": d["id"]}
    for m in an.get("metrics", []): metric_maql[m["id"]] = (m.get("content") or {}).get("maql", "")

    used_cols = {}  # master column id -> formula  (raw cols + cross-element dims)
    def col(name, formula):
        cid = re.sub(r'[^a-z0-9]', '_', name.lower())
        used_cols[cid] = {"id": cid, "name": name, "formula": formula}
        return cid

    def dim_ref(attr_id):
        a_ = attr[attr_id]
        if a_["ds"] == a.fact_dataset:
            return f"[{P}/{a_['title']}]"
        return f"[{P}/{a.rel_name}/{a_['title']}]"  # cross-element via relationship

    # recursively resolve a metric's MAQL to a Sigma workbook aggregate formula
    def resolve(maql):
        body = re.sub(r"^\s*SELECT\s+", "", " ".join(maql.split()), flags=re.I).strip()
        if re.search(r"BY ALL|WITHIN|\bFOR \b", body, re.I):
            return None  # flagged context/time
        out = re.sub(r"COUNT\(\s*\{attribute/([^}]+)\}\s*\)",
                     lambda m: f"CountDistinct([{P}/{attr[m.group(1)]['title']}])", body, flags=re.I)
        out = re.sub(r"\{fact/([^}]+)\}", lambda m: f"[{P}/{fact[m.group(1)]['title']}]", out)
        out = re.sub(r"(SUM|AVG|MIN|MAX|MEDIAN)\(([^()]*)\)",
                     lambda m: f"{AGG[m.group(1).upper()]}({m.group(2)})", out, flags=re.I)
        out = re.sub(r"\{metric/([^}]+)\}", lambda m: f"({resolve(metric_maql[m.group(1)])})", out)
        return out

    insights = {i["id"]: i for i in an["visualizationObjects"]}
    KIND = {"local:headline": "kpi", "local:bar": "bar", "local:column": "bar"}
    SRC = {"kind": "data-model", "dataModelId": a.data_model_id, "elementId": a.fact_element}
    page_elements = []; flags = []
    cid = lambda n: re.sub(r'[^a-z0-9]', '_', n.lower())

    def insight_measure(ins):
        item = ins["content"]["buckets"][0]["items"][0]["measure"]
        mid = item["definition"]["measureDefinition"]["item"]["identifier"]["id"]
        return mid, item.get("title", mid), resolve(metric_maql[mid])

    # each insight sources the DM fact element directly; charts auto-aggregate by axis
    for iid, ins in insights.items():
        url = ins["content"]["visualizationUrl"]; kind = KIND.get(url); title = ins["title"]
        mid, mtitle, mformula = insight_measure(ins)
        if mformula is None:
            flags.append({"insight": iid, "reason": f"measure {mid} uses flagged MAQL"}); continue
        mc = cid(mtitle)
        if kind == "kpi":
            page_elements.append({"id": iid, "kind": "kpi-chart", "name": title, "source": SRC,
                "columns": [{"id": mc, "formula": mformula, "name": mtitle}], "value": {"columnId": mc}})
        elif kind == "bar":
            aid = ins["content"]["buckets"][1]["items"][0]["attribute"]["displayForm"]["identifier"]["id"].rsplit(".", 1)[0]
            dname = attr[aid]["title"]; dc = cid(dname)
            page_elements.append({"id": iid, "kind": "bar-chart", "name": title, "source": SRC,
                "columns": [{"id": dc, "formula": dim_ref(aid), "name": dname},
                            {"id": mc, "formula": mformula, "name": mtitle}],
                "xAxis": {"columnId": dc}, "yAxis": {"columnIds": [mc]},
                **({"orientation": "horizontal"} if url == "local:bar" else {})})

    # ---- TIME_INTEL: FOR PREVIOUS/NEXT metrics -> date-grouped trend + DateLookback ----
    # Wire each dateInstance to the dataset/column that holds its DATE column
    # (a reference whose source target.type == "date"); the fact reaches it via
    # the relationship named after that dataset's table (convert.py convention).
    date_for_di = {}   # dateInstance id -> (date_dataset_id, date_source_column)
    for d in ldm["datasets"]:
        for r in d.get("references", []):
            for s in r.get("sources", []):
                if (s.get("target") or {}).get("type") == "date":
                    date_for_di[s["target"]["id"]] = (d["id"], s["column"])
    tbl = {d["id"]: d["dataSourceTableId"]["id"] for d in ldm["datasets"] if d.get("dataSourceTableId")}
    UNITS = {"day", "week", "month", "quarter", "year"}

    def date_ref(di_id):
        ds_id, scol = date_for_di[di_id]
        return f"[{P}/{tbl[ds_id]}/{scol.replace('_', ' ').title()}]"  # [FACT/DATE_DIM/Full Date]

    for m in an.get("metrics", []):
        mm = metric_maql.get(m["id"], "")
        fp = re.search(r"FOR\s+(PREVIOUS|NEXT)\(\s*\{label/([^.}]+)\.(\w+)\}(?:\s*,\s*(\d+))?\s*\)", mm, re.I)
        base = re.search(r"\{metric/([^}]+)\}", mm)
        if not (fp and base):
            continue
        direction, di_id, gran, n = fp.group(1).lower(), fp.group(2), fp.group(3).lower(), int(fp.group(4) or 1)
        if di_id not in date_for_di or gran not in UNITS:
            flags.append({"metric": m["id"], "reason": f"FOR {direction}: date dim/granularity not resolvable"}); continue
        base_formula = resolve(metric_maql[base.group(1)])
        if base_formula is None:
            flags.append({"metric": m["id"], "reason": "FOR PREVIOUS base metric not translatable"}); continue
        off = n if direction == "previous" else -n
        eid = cid(m["id"])
        page_elements.append({"id": eid, "kind": "table", "name": m.get("title") or m["id"], "source": SRC,
            "columns": [
                {"id": "ti_period", "name": gran.capitalize(), "formula": f'DateTrunc("{gran}", {date_ref(di_id)})'},
                {"id": "ti_base", "name": base.group(1), "formula": base_formula},
                {"id": "ti_prior", "name": m.get("title") or m["id"],
                 "formula": f'DateLookback({base_formula}, [{gran.capitalize()}], {off}, "{gran}")'}],
            "groupings": [{"id": "ti_g", "groupBy": ["ti_period"], "calculations": ["ti_base", "ti_prior"]}]})

    spec = {"name": layout.get("name") or "GoodData Migration", "schemaVersion": 1, "folderId": a.folder_id,
            "pages": [{"id": "p1", "name": an["analyticalDashboards"][0]["title"], "elements": page_elements}]}
    json.dump(spec, open(a.out, "w"), indent=2)
    print(f"workbook -> {a.out}: master + {len(page_elements)} elements ({len(used_cols)} master cols), {len(flags)} flagged")
    for e in page_elements: print("   ", e["kind"], e["name"])
    for fl in flags: print("   FLAG", fl)


if __name__ == "__main__":
    main()
