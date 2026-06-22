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

    ds_table = {d["id"]: d["dataSourceTableId"]["id"] for d in ldm["datasets"] if d.get("dataSourceTableId")}

    def dim_ref(attr_id):
        a_ = attr[attr_id]
        if a_["ds"] == a.fact_dataset:
            return f"[{P}/{a_['title']}]"
        # cross-element via the relationship named after the dim's table (convert.py convention)
        return f"[{P}/{ds_table[a_['ds']]}/{a_['title']}]"

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
    # local: viz url -> Sigma element kind. local:table is table OR pivot-table
    # (decided by presence of a "columns" bucket). Unmapped -> flagged table.
    CHART = {"local:bar": "bar-chart", "local:column": "bar-chart", "local:line": "line-chart",
             "local:area": "area-chart", "local:donut": "donut-chart", "local:pie": "pie-chart"}
    FLAGGED = {"local:funnel", "local:pyramid", "local:sankey", "local:dependencywheel",
               "local:waterfall", "local:treemap", "local:repeater", "local:bullet"}
    SRC = {"kind": "data-model", "dataModelId": a.data_model_id, "elementId": a.fact_element}
    page_elements = []; flags = []
    cid = lambda n: re.sub(r'[^a-z0-9]', '_', n.lower())

    def measures_of(ins):
        out = []
        for b in ins["content"]["buckets"]:
            if b["localIdentifier"] == "measures":
                for it in b["items"]:
                    mid = it["measure"]["definition"]["measureDefinition"]["item"]["identifier"]["id"]
                    out.append((mid, it["measure"].get("title", mid), resolve(metric_maql[mid])))
        return out

    def dims_of(ins, kinds):
        out = []
        for b in ins["content"]["buckets"]:
            if b["localIdentifier"] in kinds:
                for it in b["items"]:
                    aid = it["attribute"]["displayForm"]["identifier"]["id"].rsplit(".", 1)[0]
                    out.append(aid)
        return out

    for iid, ins in insights.items():
        url = ins["content"]["visualizationUrl"]; title = ins["title"]
        if url in FLAGGED:
            flags.append({"insight": iid, "url": url, "reason": f"{url} has no Sigma equivalent → migrate as table or skip"}); continue
        meas = measures_of(ins)
        if any(f is None for _, _, f in meas):
            flags.append({"insight": iid, "reason": "measure uses workbook-level MAQL (BY ALL / FOR)"}); continue
        mcols = [{"id": cid(t) or m, "formula": f, "name": t} for m, t, f in meas]

        if url == "local:headline":          # KPI
            page_elements.append({"id": iid, "kind": "kpi-chart", "name": title, "source": SRC,
                "columns": mcols[:1], "value": {"columnId": mcols[0]["id"]}})
        elif url == "local:table":            # table (flat) or pivot-table (has columns shelf)
            rows = dims_of(ins, {"attribute", "view"}); colshelf = dims_of(ins, {"columns"})
            dcols = [{"id": cid(attr[a_]["title"]), "formula": dim_ref(a_), "name": attr[a_]["title"]} for a_ in rows + colshelf]
            if colshelf:                      # pivot
                page_elements.append({"id": iid, "kind": "pivot-table", "name": title, "source": SRC,
                    "columns": dcols + mcols, "values": [c["id"] for c in mcols],
                    "rowsBy": [{"id": cid(attr[a_]["title"])} for a_ in rows],
                    "columnsBy": [{"id": cid(attr[a_]["title"])} for a_ in colshelf]})
            else:                             # flat aggregated table
                page_elements.append({"id": iid, "kind": "table", "name": title, "source": SRC,
                    "columns": dcols + mcols,
                    "groupings": [{"id": "g", "groupBy": [c["id"] for c in dcols], "calculations": [c["id"] for c in mcols]}]})
        elif url in CHART:                    # bar/column/line/area/donut/pie
            kind = CHART[url]; dims = dims_of(ins, {"view", "segment", "stack"})
            dcols = [{"id": cid(attr[a_]["title"]), "formula": dim_ref(a_), "name": attr[a_]["title"]} for a_ in dims]
            el = {"id": iid, "kind": kind, "name": title, "source": SRC, "columns": dcols + mcols}
            if kind in ("donut-chart", "pie-chart"):
                # donut/pie kept value/color as {id} (only KPI moved to columnId, 2026-06-11)
                el["value"] = {"id": mcols[0]["id"]}
                if dcols: el["color"] = {"id": dcols[0]["id"]}
            else:
                if dcols: el["xAxis"] = {"columnId": dcols[0]["id"]}
                el["yAxis"] = {"columnIds": [c["id"] for c in mcols]}
                if url == "local:bar": el["orientation"] = "horizontal"
            page_elements.append(el)
        else:
            flags.append({"insight": iid, "url": url, "reason": f"unmapped visualizationUrl {url}"})

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
