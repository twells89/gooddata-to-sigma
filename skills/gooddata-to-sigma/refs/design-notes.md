# gooddata-to-sigma — architecture, parity, risks, build order

## Pipeline (mirrors the sibling converters)

```
Phase 0  Assess        gooddata-assessment: inventory + readiness readout
Phase 1  Discover      discover.py → GET /api/v1/layout/workspaces/{id} → workspace_layout.json
Phase 2  Data model    LDM datasets/attributes/facts/references → Sigma DM
                        MAQL metrics → Sigma DM metrics/formulas (maql-mapping.md)
                        recover warehouse path from dataSource → same connection for parity
Phase 3  Workbook      insights → elements, dashboards → pages + layout (viz-type-mapping.md)
                        filterContext → controls; defer chart authoring to sigma-workbooks
Phase 4  Parity        post-and-readback + assert-parity vs the SAME warehouse
Phase 5  Repoint        finalize workbook sources onto the built DM (never skip)
Phase 6  Enhance/QA    mandatory visual-QA PNG gate; theme via theme-registry
```

## What's new code vs reused

**New** (this repo): GoodData declarative client (`discover.py`), LDM→DM mapper,
**MAQL translator** (the hard part), insight/dashboard→workbook signal builder,
the assessment scanner.

**Reused** (from sigma-migration-skills/shared + sigma-workbooks): the workbook
spec authoring (sigma-workbooks), `find-or-pick-dm`, `build-charts-from-signals`,
layout `decollide_bands` + visual-QA gate, `gap-scout` / `learned-rules` /
`escalate-gap`, the RLS detect→ask→provision→emit gate, the theme registry, and
the `convert_*_to_sigma_formula` plumbing the MAQL translator extends.

Also: a `convert_gooddata_to_sigma` MCP converter + browser mirror, kept in sync
(per the cross-converter convention).

## RLS — user data filters (UDF)

GoodData **User Data Filters** restrict rows per user via a `maql` predicate +
user assignments (the Cloud equivalent of legacy MUF). Maps to the established
Sigma pattern: detect UDFs → map the predicate attribute to a Sigma
**user attribute** → emit a DM filter `CurrentUserAttributeText("x") = [Col]` →
one consolidated gate, opt-in/out, never silent. Workspace Data Filters
(multi-tenant child-workspace column filter) → a single user-attribute filter on
the tenant column.

## Parity strategy

GoodData Cloud computes on the customer warehouse, so the same-warehouse parity
play works. Caveats to prove out live:
- **FlexQuery / compute-only metrics** may have no clean SQL equivalent — flag,
  don't fake; confirm which MAQL constructs round-trip to warehouse SQL.
- Apply dashboard `filterContext` when checking insight totals.
- `sql`-backed datasets → Custom-SQL element parity (watch the known
  custom-SQL-via-spec limitation; prefer a join/warehouse-table where possible).

## Risks (ranked)

1. **MAQL coverage** — `BY` / `BY ALL` / `WITHIN` context + `FOR` time intel.
   Build gap-scout first so coverage is measured, not assumed.
2. **Compute-engine-only metrics** — no warehouse equivalent → flag set.
3. **Cloud vs classic Platform** API divergence — Cloud first; classic deferred.
4. **Dashboard layout fidelity** — responsive grid, not pixels; map to Sigma grid.
5. **Exotic visualizations** (funnel/sankey/waterfall/treemap) → flagged tables.

## Build order

1. **Trial + live discovery** — GoodData Cloud trial, validate token auth +
   `GET /layout/workspaces/{id}`, dump a real workspace as the fixture
   (ThoughtSpot/Sisense bootstrap pattern).
2. LDM→DM mapper + plain-MAQL translator → first live DM with parity.
3. Insights→workbook (headline/table/bar/line first) → first live workbook.
4. MAQL context + time intel (gap-scout-driven).
5. Dashboards→layout, filterContext→controls, UDF→RLS.
6. Assessment skill readout; then graduate into sigma-migration-skills/plugins/.

## Scope note

GoodData **Cloud / .CN** only for v0.1. Legacy **GoodData Platform**
(`/gdc/md/{project}`, classic MAQL dialect, MUF) is a separate extraction client
— documented fast-follow, not built.
