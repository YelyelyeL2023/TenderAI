# Risks and limitations

## Data

| # | Risk / limitation | Effect | Mitigation in the system |
|---|---|---|---|
| D1 | **Two ENSTRU code formats** (old KPVED-based 37-character codes, new `NNNNNN.NNN.NNNNNN`) with no mapping table in OWS | The same item can sit under two codes, which shrinks comparison samples | Exact code first, then a prefix fallback within the format; the answer states the comparison level. Next step: a crosswalk via `RefEnstru.parentId` and name similarity |
| D2 | **Broad ENSTRU codes**: one code covers different specifications and pack sizes | Large price dispersion inside a code (median abs log deviation ≈ 0.47 for goods); the 30% rule alone flags about 30% of positions | Leave-one-out baseline plus robust log-z confirmation tier; Fair Price uses the median and IQR band; confidence drops with dispersion |
| D3 | **Delivery location missing** on ≈70% of plan points | The "same city" comparison is not always possible | Fallback chain with `kato_source`; inferred locations are stated as a limitation; the hierarchy widens to region or national |
| D4 | **Services and works priced per "1 service"** | The unit price equals the contract value | Excluded from unit-price anomalies by default (option to include); Fair Price for them is low-confidence |
| D5 | **Planned vs actual**: volume anomalies use plan quantities | Plans can be amended or not executed | Latest plan versions only; stated in every volume answer |
| D6 | **Short history** (2024–2026) | Only one baseline year for 2025; seasonality is rarely estimable | Ratio rule with a z-test only when ≥ 2 previous years exist; seasonality only when N ≥ 30 across ≥ 6 months |
| D7 | **Only 27 customers in scope**, so "other agencies" means the other 26 | Thin comparison sets for niche items | N and comparison level shown; the hierarchy falls back to prefix; loading more customers is a config change (`TARGET_BINS`) |
| D8 | **CPI is not in OWS**, and bulletins cover 01.2024–09.2026 only | Months after September 2026 (and the derived December 2023) use the nearest covered level | Official stat.gov.kz bulletins loaded by `load-cpi` (regional, by category); the answer names the index, category and factor range. Add each new monthly bulletin to `cpi_dataset/` and re-run `load-cpi` |
| D9 | **Lot ↔ plan point link through `pointList[0]`**: multi-point lots map to their first point | Possible unit or ENSTRU mismatch on combined lots | DQ rule `lot_price_differs_from_plan` (>50% gap) flags these |
| D10 | **Contract line totals inconsistent** (`totalSum ≠ itemPrice × quantity`) | Ambiguity about the actual price | `itemPrice` is used; DQ warning |

## API and operations

| # | Risk | Mitigation |
|---|---|---|
| A1 | Token expiry (tokens last 1 year) or revocation returns 403 | The client raises a clear `OWS access denied — check OWS_TOKEN`; `/health` shows the last ingest status |
| A2 | Transient timeouts and 5xx (seen during the backfill) | Exponential backoff (6 attempts); idempotent upserts make re-runs safe; per-BIN reconciliation against API `totalCount` |
| A3 | GraphQL schema changes (field renames, as in the documented camelCase migration) | Raw payloads are stored verbatim; an introspection snapshot (`tests/fixtures/ows/graphql_schema.json`) supports diffing |
| A4 | Rate limits are undocumented | Concurrency of 4 and paging of 200 rows; incremental runs need about 110 requests |
| A5 | Journal misses or delays | 2 h overlap on `indexDate`; a nightly re-run of the backfill reconciles (idempotent) |
| A6 | Data lag SLA (≤ 24 h) | Sync every 3 h; `/health` reports `lag_hours` and `freshness_sla_ok` |

## AI agent

| # | Risk | Mitigation |
|---|---|---|
| L1 | **LLM hallucinating figures** | The LLM never computes. Every number, BIN and date in the verdict is validated against the tool facts (one retry, then the template verdict). All other sections are rendered from tool output |
| L2 | **Misclassified intent** | Hard-fact regexes (lot number → fair price, etc.) override the LLM; the rule router works without the LLM; intent accuracy is measured in `eval/` |
| L3 | **Wrong item resolution** (free text → ENSTRU) | Tiered matching (exact name → phrase → all words → trigram ≥ 0.45), most-purchased product first within a tier, so "бензин" is motor petrol and not "бензидамин"; variant codes (АИ-92 / АИ-95, summer / winter diesel, A4 / A3) are told apart by their plan description, a bare product name covers every variant and prices are reported per variant; the resolved codes and variants are shown in "Search parameters" so the user can check. Remaining gaps: Kazakh item names the LLM fails to translate, abbreviations ("дизтопливо") |
| L3c | **Misread item** (e.g. the classifier's own name "ЕНСТРУ" taken as the item) | Classifier words are never items; an item that is neither a product code nor any purchase name is reported as "не распознан" instead of producing an empty "no data" answer |
| L3d | **Intermittent misreadings** (the LLM is not fully deterministic) | `run_eval.py --repeats N` and the paraphrase set (`eval/paraphrases.jsonl`) measure whether every wording and every run gives the same facts |
| L3a | **Wrong customer** (a name that matches nothing, or matches several) | Word-by-word matching with official forms collapsed; one best match per name; an unmatched name is refused instead of silently widening the query to all 27 customers (which would credit their totals to the named one) |
| L3b | **Right number, wrong meaning** in the LLM verdict | Claim checks on top of the number check: the compared total cannot be presented as the flagged count, a price outside Q1–Q3 cannot be called inside it; "ИНН" on an organisation's number is corrected to "БИН" |
| L4 | **Weaker Kazakh** in a 9B model | Language detection by rules; Kazakh answers use fixed Kazakh section templates; only the verdict is generated |
| L5 | **Single local GPU (8 GB)**: one request at a time | The API serialises LLM calls; the model is kept loaded for 30 minutes; template answers work if the LLM is down |
| L6 | **Prompt injection through data** (lot names are user-supplied text) | Names reach the LLM only inside a JSON facts block; the LLM has no tools, SQL or network access; output is validated |

## Interpretation

- A price "anomaly" is a **signal for review**, not proof of a violation. Specifications, delivery terms and urgency
  can justify a deviation.
- The Fair Price band reflects prices actually paid by the other 26 customers. It is not an external market benchmark.
