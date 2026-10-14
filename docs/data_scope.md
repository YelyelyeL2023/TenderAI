# Data scope: OWS v3 entities, rationale and API findings

## Entities used

| Entity (OWS v3) | How it is loaded | Why it is needed |
|---|---|---|
| **Plans** (plan points, `PlnPoint`) | GraphQL `Plans(filter:{subjectBiin, plnPointYear:[2024,2025,2026]})`, plus every plan point referenced by a stored lot or contract line (fetched by `id`) | Source of the **ENSTRU code, unit, planned price and quantity, and delivery KATO** (`PlansKato`). Basis for volume anomalies |
| **Announcements** (`TrdBuy`) | `TrdBuy(filter:{orgBin, publishDate})`, plus announcements referenced by in-scope lots but organised by a third party (fetched by `id`) | Publication date, trade method, status, KATO; portal link |
| **Lots** (`Lots`) | `Lots(filter:{customerBin})` (the API has no date filter on lots; scope is applied in `core.lot.in_scope`) | Announced price and quantity, link to plan point (`pointList`) and announcement |
| **Contracts** with **line items** (`Contract` → `ContractUnits`) | `Contract(filter:{customerBin, signDate})` | **Actual unit prices** (`itemPrice` without VAT), quantities, supplier, status, amendment chain |
| **Subjects** (customers, suppliers) | `Subjects(filter:{bin})` / `{iin}` for the 27 customers and every supplier on a contract | Names, registered address KATO (fallback location), supplier flags |
| **Dictionaries** | REST `/v3/refs/{name}` (≤ 500 rows per page) | `ref_units` (МКЕИ), `ref_kato`, `ref_trade_methods`, `ref_buy_status`, `ref_lots_status`, `ref_contract_status`, `ref_pln_point_status`, `ref_subject_type`, `ref_months`, `ref_finsource`, `ref_type_trade`, `ref_contract_type` |
| **ENSTRU dictionary** | Nested `RefEnstru` on each plan point | `/v3/refs/ref_enstru` returns "Справочник не найден" in v3 |
| **Journal** | REST `/v3/journal?date_from&date_to` | **Deletions** for the incremental sync (`action = D`) |
| **Official CPI** (outside OWS) | stat.gov.kz monthly bulletins T-15-02-M (33 .xlsx files, 01.2024–09.2026) → `tenderai load-cpi cpi_dataset` → `core.cpi` | Inflation adjustment for Fair Price and constant-price anomaly baselines: 21 regions (20 + national) × 4 categories (all, food, non-food, services), fixed base December 2020 = 100 |

Not used: RNU (unscrupulous suppliers), complaints, acts, treasury payments and waybills. They would enrich supplier
risk scoring but are not needed for the required question classes. The loader is generic, so adding one is a single
entry in `tenderai/ingest/queries.py`.

## Volumes (initial backfill, 2026-10-12)

| Entity | Rows |
|---|---|
| plan points in scope (27 BINs × 2024–2026) | 67,078 |
| plan points referenced from other years or BINs | ≈134,000 |
| announcements | 36,835 |
| lots (all years, raw) / in scope | 272,909 / ≈117,000 |
| contracts / line items | 36,810 / 105,251 |
| suppliers and customers | ≈9,100 |

## API findings

1. **The token in the public documentation is a sample.** `…abb12b` appears in every example and returns
   `403 Access denied`. A working token is issued separately.
2. **Use GraphQL rather than REST.** GraphQL lots carry the name, quantity, amount, ENSTRU list and delivery KATO,
   while REST v3 lots only carry links. GraphQL also supports server-side filters by BIN, year and `indexDate`.
3. **Paging.** The maximum is **200 rows per GraphQL page** (larger `limit` values are capped silently). The cursor is
   `after = extensions.pageInfo.lastId`, with rows ordered by id descending. REST dictionaries allow 500 per page.
4. **Errors can arrive with HTTP 200** (`{"error": "Справочник не найден", "code": 404}`), so the client checks the
   response body as well as the status.
5. **Date filters** accept `YYYY-MM-DD` and ISO `YYYY-MM-DDTHH:MM:SS`. A space-separated time returns nothing.
   API timestamps are local time (UTC+5).
6. **REST returns double-encoded UTF-8** in some text fields (addresses, `service_title`). GraphQL text is correct,
   which is another reason it is the primary path.
7. **Transient failures.** Read timeouts and HTTP 5xx occurred in bursts during the backfill. All were absorbed by
   exponential-backoff retries (≤ 6 attempts).
8. **The journal is national and its volume swings.** About 105,000 changes on a normal day (210 pages × 500), but
   4,637,510 on 2026-10-12 (9,276 pages), when the portal re-indexed plans in bulk. The incremental sync therefore
   re-queries only the 27 customers by `indexDate` and reads the journal just for deletions, filtered on the server
   with `action=D` (372,598 deletions that day, 746 pages). `date_to` is exclusive. Because the filtered journal does
   not show re-publication, objects whose stored version was re-indexed inside the sync window are never soft-deleted.

## Data-quality findings

| Finding | Impact | Handling |
|---|---|---|
| ENSTRU codes come in two formats: new `192026.510.000000` and old `10.39.11.100.010.00.0166.000000000000` | Items cannot be matched across formats by code | Exact match first, then a prefix fallback within the same format |
| Lots have `enstruList: [0]` | No ENSTRU code on the lot | Taken from the linked plan point (`pointList[0]`) |
| ≈70% of plan points have no `PlansKato` (null in both REST and GraphQL) | Delivery city unknown | Fallback chain lot → plan → announcement → customer address, with `kato_source` recorded |
| Announcements often have `customerBin = null` (only the organiser's BIN) | Customer unknown at announcement level | Customer taken from lots |
| Contract amendment chains (`parentId` / `rootId`) | The same contract appears several times | Only the final version (`is_latest`) counts |
| `totalSum ≠ itemPrice × quantity` on some contract lines | Ambiguous line totals | `itemPrice` used; DQ warning |
| Services and works priced per "1 service" | Unit price equals contract value, not comparable | Excluded from unit-price anomalies by default |
