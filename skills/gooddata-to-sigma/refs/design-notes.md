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

## Date dimensions & FOR PREVIOUS — LIVE-VALIDATED, exact parity (all via API)

**Closed 2026-06-22, entirely via the API (no UI).** GoodData date dimension +
`FOR PREVIOUS` metric built through the declarative/entity APIs; migrated to a
Sigma monthly-trend element whose `DateLookback` prior-month column matches the
Snowflake monthly baseline **exactly** (2024-02 prev = 2,947.38 = Jan, etc.).

Cracked recipe:
- **Date reference shape** (the blocker): a dataset links a date dimension via a
  normal `references` entry with `identifier.type: "dataset"` (pointing at the
  dateInstance id) and a DATE source column whose **`sources[].target.type` is
  `"date"`** (NOT `"dateInstance"` — that was the 400). `dateInstances[]` defines
  the dimension (`granularities: DAY/WEEK/MONTH/QUARTER/YEAR`). Put the date ref
  on the dataset that owns a DATE column (here `DATE_DIM.FULL_DATE`); bridge the
  fact via a normal `order→DATE_DIM` reference on the INT key.
- **MAQL time metric:** `SELECT {metric/m_net_revenue} FOR PREVIOUS({label/order_date.month})`.
- **Converter (`convert.py`):** skip date references when building Sigma
  *relationships* (target.type=="date") — the date column is just a plain column
  (the FK-column pass already surfaces `FULL_DATE` as a column, reachable from
  the fact via the `DATE_DIM` relationship).
- **Sigma output:** date-grouped element (`DateTrunc("month", [FACT/DATE_DIM/Full Date])`)
  with `DateLookback(Sum([FACT/Net Revenue]), [Month], 1, "month")` for prior month.
  This is the auto-emit target for `build_workbook` on a TIME_INTEL metric (recipe
  proven; generalization is the small remaining converter step).

Original findings (for reference):

- **GoodData date dimensions are "Date datasets" = `dateInstances`** in the LDM.
  `granularities` enum (validated): `MINUTE, HOUR, DAY, WEEK, MONTH, QUARTER,
  YEAR, MINUTE_OF_HOUR, HOUR_OF_DAY, DAY_OF_WEEK, DAY_OF_MONTH, DAY_OF_QUARTER,
  DAY_OF_YEAR, WEEK_OF_YEAR, MONTH_OF_YEAR, QUARTER_OF_YEAR, FISCAL_MONTH,
  FISCAL_QUARTER, FISCAL_YEAR` (NOT `WEEK_SUN_SAT`).
- **A date link is NOT a normal `references` entry.** A `references[].identifier.type`
  only accepts `dataset` (rejects `dateInstance`). Auto-generated models link a
  date dimension by a **column-naming convention** (`d__date`, with a granularity
  suffix like `d__date__day`), so the manual declarative date-reference shape
  still needs to be harvested from a real workspace that has one (build a date
  dim in the UI once, GET the LDM) — same harvest-from-example tactic used for
  plugins/themes elsewhere.
- **`ORDER_FACT` exposes only INT date keys** (`ORDER_DATE_KEY` YYYYMMDD, no DATE
  column). Two ways to get a DATE: a SQL-backed dataset that casts it, or join
  `CSA.TJ.DATE_DIM` (has `FULL_DATE` DATE + `DATE_KEY`).
- **Converter work to add:** `dateInstance` → a Sigma date column (parse YYYYMMDD
  via the date DSL, or use the joined `FULL_DATE`); `FOR PREVIOUS` →
  `DateLookback(<measure>, "month", -1)` in a date-grouped workbook element
  (build_workbook addition). Parity target: prior-period net revenue vs Snowflake.

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
