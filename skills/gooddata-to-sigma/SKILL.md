---
name: gooddata-to-sigma
description: >-
  Migrate GoodData Cloud / GoodData.CN workspaces to Sigma. Use when the user
  has a GoodData workspace — datasets, MAQL metrics, insights, and analytical
  dashboards — and wants to recreate it in Sigma. Exports the workspace via the
  declarative layout API (logicalModel + analyticsModel), maps the LDM
  (datasets / attributes / facts / references) to a Sigma data model, translates
  MAQL metrics to Sigma formulas, maps insights to workbook charts/KPIs/pivots
  and dashboards to pages + layout, ports user data filters (RLS) to Sigma user
  attributes, and verifies parity against the same warehouse. Translates what
  maps cleanly and flags what doesn't (compute-engine-only metrics, exotic MAQL
  context, unsupported widgets) instead of emitting wrong logic. Legacy GoodData
  Platform (/gdc/md classic) is out of scope for now — Cloud / .CN only.
user-invocable: true
---

# GoodData → Sigma

> **Status: research spike complete, converter not yet built or live-validated.**
> The design is documented and verified against GoodData's docs; the discovery
> client is functional but unproven on a live workspace. Build order and risks
> are in `refs/design-notes.md`. **Do not claim a conversion works until it has
> passed live parity.**

Recreate a GoodData workspace in Sigma, in the same phase structure as the
sibling converters (Tableau, Power BI, Qlik, Cognos, MicroStrategy, SSRS, …).
This skill defers all workbook-spec authoring to the **sigma-workbooks** skill
and all data-model authoring to **sigma-data-models**.

## Read these first

- `refs/gooddata-api.md` — declarative export API, auth, LDM + analytics shape.
- `refs/maql-mapping.md` — MAQL → Sigma formula contract (the hard part).
- `refs/viz-type-mapping.md` — insight + dashboard → Sigma element mapping.
- `refs/design-notes.md` — full architecture, parity, RLS, risks, build order.

## Phases

**Phase 0 — Assess.** Run the `gooddata-assessment` skill for an inventory +
readiness readout before committing to a conversion.

**Phase 1 — Discover.** `eval "$(scripts/get-token.sh)"` then
`python3 scripts/discover.py --workspace <id>` → `workspace_layout.json`
(full LDM + analytics model). Confirm counts and the MAQL-keyword / insight-type
histograms it prints.

**Phase 2 — Data model.** Map LDM datasets → Sigma sources (recover the
warehouse path from the data-source mapping so parity runs on the same
warehouse), attributes/labels → dimension columns, facts → numeric columns,
references → relationships. Translate MAQL metrics → Sigma DM metrics/formulas
per `maql-mapping.md`. Build the DM via the Sigma REST API (sigma-data-models).

**Phase 3 — Workbook.** Map insights → elements and dashboards → pages + layout
per `viz-type-mapping.md`; `filterContext` → controls scoped to referencing
pages. Author the workbook spec via **sigma-workbooks** (formula qualification,
the `name: ' '` KPI-title rule, theming, layout). Reuse the shared
`build-charts-from-signals` + `decollide_bands` + the mandatory visual-QA gate.

**Phase 4 — Parity.** Post-and-readback + assert-parity for every metric/insight
against the **same warehouse**; apply dashboard filter context when checking.

**Phase 5 — Repoint.** Finalize workbook element sources onto the built DM —
never skip.

**Phase 6 — RLS + enhance.** Port user data filters → Sigma user attributes via
the consolidated RLS gate; apply theme via the theme registry; final visual-QA.

## Contract: flag, never fake

Surface — never silently mis-convert — these (log to gap-scout / learned-rules,
opt-in escalate):
- MAQL with no clean warehouse equivalent (FlexQuery / compute-only).
- `BY` / `BY ALL` / `WITHIN` context that can't be reconstructed from insight grain.
- Exotic visualizations (funnel / sankey / waterfall / treemap …) → flagged table.
- `sql`-backed datasets and anything the MAQL parser tags `UNHANDLED`.

## Scope

GoodData **Cloud / .CN** (`/api/v1` declarative API). Legacy **Platform**
(`/gdc/md/{project}`, classic MAQL, MUF) is a documented fast-follow — see
`design-notes.md` — not built.
