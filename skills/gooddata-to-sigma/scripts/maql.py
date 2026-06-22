#!/usr/bin/env python3
"""maql.py — MAQL → Sigma formula translator.

Scoped to the constructs the converter handles cleanly; everything else is
returned as UNHANDLED with the raw MAQL (flag, never fake). The hard MAQL
surface — BY / WITHIN / BY ALL context and FOR time transforms — is detected and
flagged here, to be measured by gap-scout, not silently mis-translated.

translate(maql, syms) -> {"ok": bool, "formula": str|None, "reason": str|None}
  syms = {"fact": {id: "Display Name"}, "attribute": {id:.}, "metric": {id:.}}
"""
import re

AGG = {"SUM": "Sum", "AVG": "Avg", "MIN": "Min", "MAX": "Max", "MEDIAN": "Median"}
# context / time keywords with no clean pure-metric Sigma equivalent (workbook-level)
FLAG_KW = ["BY ALL", "WITHIN", "FOR PREVIOUS", "FOR NEXT", "FOR ", " BY "]


def _ref(syms, kind, gid):
    name = syms.get(kind, {}).get(gid)
    return f"[{name}]" if name else None


def translate(maql, syms):
    src = " ".join(maql.split())
    body = re.sub(r"^\s*SELECT\s+", "", src, flags=re.I).strip()

    # flag the hard context/time surface before anything else
    up = body.upper()
    for kw in FLAG_KW:
        if kw in up:
            return {"ok": False, "formula": None,
                    "reason": f"MAQL context/time keyword '{kw.strip()}' has no pure data-model-metric equivalent (workbook-level); flagged"}

    out = body
    # COUNT({attribute/x}) -> CountDistinct([Name])  (GoodData COUNT of an attribute = distinct)
    def count_attr(m):
        r = _ref(syms, "attribute", m.group(1))
        return f"CountDistinct({r})" if r else m.group(0)
    out = re.sub(r"COUNT\(\s*\{attribute/([^}]+)\}\s*\)", count_attr, out, flags=re.I)

    # AGG({fact/x}) and AGG(<expr of facts>) -> Sigma agg
    def agg_fact(m):
        fn = AGG[m.group(1).upper()]
        inner = m.group(2)
        return f"{fn}({inner})"
    # first resolve fact refs to column names inside any expression
    def fact_ref(m):
        r = _ref(syms, "fact", m.group(1))
        return r if r else m.group(0)
    out = re.sub(r"\{fact/([^}]+)\}", fact_ref, out)
    out = re.sub(r"(SUM|AVG|MIN|MAX|MEDIAN)\(([^()]*)\)", agg_fact, out, flags=re.I)

    # metric refs -> reference other DM metrics by name
    def metric_ref(m):
        r = _ref(syms, "metric", m.group(1))
        return r if r else m.group(0)
    out = re.sub(r"\{metric/([^}]+)\}", metric_ref, out)

    # leftover unresolved object refs => unhandled
    leftover = re.search(r"\{(fact|attribute|metric|label)/([^}]+)\}", out)
    if leftover:
        return {"ok": False, "formula": None,
                "reason": f"unresolved MAQL reference {leftover.group(0)}"}

    return {"ok": True, "formula": out.strip(), "reason": None}


if __name__ == "__main__":
    syms = {"fact": {"net_revenue": "Net Revenue", "net_profit": "Net Profit"},
            "attribute": {"order_id": "Order Id", "region": "Region"},
            "metric": {"m_net_revenue": "Net Revenue", "m_order_count": "Order Count"}}
    for q in ["SELECT SUM({fact/net_revenue})",
              "SELECT COUNT({attribute/order_id})",
              "SELECT {metric/m_net_revenue} / {metric/m_order_count}",
              "SELECT {metric/m_net_revenue} / (SELECT {metric/m_net_revenue} BY ALL {attribute/region})"]:
        print(q, "->", translate(q, syms))
