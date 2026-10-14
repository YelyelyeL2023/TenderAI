# Analytics and metrics

All metrics are computed by deterministic SQL/Python (`tenderai/analytics`, `tenderai/etl/marts.sql`).
The LLM never computes; it picks a tool and phrases a verdict whose numbers are validated against the tool output.

## 0. Common conventions

| Convention | Rule |
|---|---|
| Price | Unit price **without VAT**, in KZT, per **base unit** (`price_base = price / factor`) |
| Units | МКЕИ code → (base unit, factor): g/kg/t → kg, ml/L/m³ → L, m/km → m, thousand pcs → pcs, etc. Packaging units (pack, set, box) stay as their own base and are compared only with themselves (`tenderai/etl/units.py`) |
| Observation priority | A contract line (actual price) replaces the lot it came from. Lots without a contract count as announced prices (`mart.price_obs`) |
| Contract versions | Only the final version of each amendment chain (`is_latest`) |
| Plan versions | Only the latest non-deleted version per `rootrecord_id` |
| Delivery location | Lot KATO → plan KATO → announcement KATO → customer's registered address (inferred, flagged in `kato_source`) |
| Region | Oblast / city of republican significance = first 2 digits of KATO |
| Excluded from statistics | DQ issues with severity `error` (non-positive price or quantity, missing unit or ENSTRU) |

## 1. Fair Price (formal definition)

For a target item *x* with ENSTRU code *e*, base unit *u*, delivery city *c* (region *r*), date *t*,
unit price *p*:

**Step 1. Comparable set S** comes from `mart.price_obs`, using the first level that has **N ≥ 5**:

1. `same_city`: same *e* and *u*, same delivery KATO *c*
2. `same_region`: same *e* and *u*, same oblast *r*
3. `national`: same *e* and *u*
4. `enstru_prefix`: a coarser ENSTRU prefix (e.g. `192026.510` → `192026`)

Always: other customers only (customer BIN ≠ target), |t − tᵢ| ≤ 18 months, pᵢ > 0.
If no level reaches N = 5, the largest set is used and the answer is marked "preliminary" with low confidence.

**Step 2. Time adjustment (inflation and seasonality)**

  pᵢ′ = pᵢ · I₍rᵢ,k₎(t) / I₍rᵢ,k₎(tᵢ) · S(mₜ) / S(mᵢ)

- *I* is the **official CPI of the Bureau of National Statistics** (stat.gov.kz monthly bulletin T-15-02-M,
  January 2024 – September 2026, loaded into `core.cpi`). It is the fixed-base level (December 2020 = 100) for
  **region rᵢ of the comparable** (20 regions; national if the region is unknown) and **CPI category k of the item**:

  | ENSTRU code starts with (KPVED division) | Unit | CPI category |
  |---|---|---|
  | 01–03, 10–11 (agriculture, food, beverages) | | food |
  | 41–43 (construction) | or "work" | all goods and services |
  | ≥ 35 (utilities, transport, services…) | or "service" | paid services |
  | otherwise | | non-food goods |

  Each comparable moves with the inflation of its own region (Astana prices with Astana CPI) up to the target
  month. Differences in price **level** between regions are handled separately in step 3.
  Months outside the bulletin coverage use the nearest covered month. December 2023 is derived from
  January 2024's month-on-month change.
- If no CPI is loaded, the fallback is the **internal procurement price index**. For each ENSTRU × unit group *g*
  observed in ≥ 3 months: r₍g,m₎ = median(ln p in month m) − median(ln p in g); then I(m) = exp(median_g r₍g,m₎).
  Normalising within each group removes the purchase-mix effect (tested in `tests/test_volumes_and_index.py`).
- *S(m)* is a monthly seasonal index: the median price in calendar month *m* divided by the overall median, shrunk
  toward 1 with weight n_m / (n_m + 10). It is applied only when N ≥ 30 and ≥ 6 distinct months are present.

**Step 3. Regional coefficient**

  K(r) = w · median(p′ in r) / median(p′) + (1 − w),  w = n_r / (n_r + 10)
  pᵢ″ = pᵢ′ · K(r_target) / K(rᵢ)

The shrinkage keeps a region with 2 observations from producing an extreme coefficient. It is applied only when
the comparable set spans more than one region.

**Step 4. Fair price and band**

  FairPrice = median(pᵢ″),  fair band = [Q1, Q3] of pᵢ″

The median is used rather than the mean because it is robust to the outliers that anomaly detection looks for.
The quantity-weighted mean is reported alongside.

**Step 5. Deviation and verdict**

  deviation = (p − FairPrice) / FairPrice
  robust z = (p − median) / (1.4826 · MAD)   (IQR / 1.349 if MAD = 0)

| Verdict | Condition |
|---|---|
| `overpriced_anomaly` | deviation > +30% **or** z > 3 |
| `underpriced_anomaly` (dumping risk) | deviation < −30% **or** z < −3 |
| `fair` | p within [Q1, Q3] **or** abs(deviation) ≤ 10% |
| `elevated` / `below_market` | otherwise, by sign |

**Step 6. Confidence** (0…1, labelled high ≥ 0.70, medium ≥ 0.45, low otherwise)

  0.35 · min(1, ln N / ln 30) + 0.25 · 1/(1 + IQR/median) + 0.20 · level score + 0.10 · (1 − DQ rate) + 0.10 · region known

The level score is 1.0 for same city, 0.9 for same region, 0.75 for national and 0.4 for an ENSTRU prefix.
If N < 5, confidence is capped at "low". Every limitation that applies is listed in the answer:
prefix fallback, small N, high dispersion, inferred location, no inflation index.

Code: `tenderai/analytics/fair_price.py`; tests with hand-checkable numbers in `tests/test_fair_price.py`.

## 2. Price anomalies: "deviation > 30% from the weighted average for the ENSTRU code"

For every observation *i* in an ENSTRU × unit group with ≥ 3 peers:

  pᵢʳᵉᵃˡ = pᵢ · I₍rᵢ,k₎(latest month) / I₍rᵢ,k₎(tᵢ)      (constant prices, official CPI of its region and category)
  peer weighted average = (Σ amountʳᵉᵃˡ − amountᵢʳᵉᵃˡ) / (Σ qty − qtyᵢ)   (quantity-weighted, **leave-one-out**)
  deviationᵢ = pᵢʳᵉᵃˡ / peer weighted average − 1

Leave-one-out stops a single outlier from pulling its own baseline up and hiding itself. Working in constant
prices means a 2024 purchase is not flagged as cheap, or a 2026 purchase as expensive, purely because of
inflation (≈ 9–13% a year in 2024–2026). In answers, the peer average is converted back to the purchase's own
month, so it can be compared directly with the nominal price.

**Robust confirmation.** ENSTRU codes are broad, and pack sizes and specifications vary. On the live data,
about 30% of all goods positions exceed ±30% of the weighted average, so the raw rule alone over-flags.
Each observation therefore also gets

  zᵢ = (ln pᵢ − median ln p) / (1.4826 · MAD(ln p))   within the group

A **confirmed** anomaly satisfies the 30% rule *and* z > 3. Answers report both counts, rank confirmed cases
first by estimated overpayment (amount − peer average × quantity), and mark them with ✔.

Services and works priced per "1 service" or "1 work" are excluded by default, because a per-unit price is not
comparable for them. Mart: `mart.price_deviation`.

## 3. Volume anomalies: "atypical increase in quantity compared with previous years"

Series: customer × ENSTRU × base unit × year, using the **planned** quantity (latest plan-point versions).

  baseline = median(quantity in previous years),  ratio = qty_Y / baseline

The series is flagged when **ratio > 1.5** and, if ≥ 2 previous years exist, robust z of ln(qty) against the
customer's own history is > 3.

With only one previous year (2025 against 2024) there is no z-score, so on the live data the ratio rule alone flags
about 22% of goods series. Answers therefore also report a **confirmed** tier: flagged **and** statistically extreme
(z > 3, or ≥ 3× growth when only one previous year exists) **and** material, meaning extra planned spend
(amount − amount / ratio) ≥ 1,000,000 KZT. On the live data that is 372 series (3.7%). Confirmed series are listed
first (✔), ranked by extra spend. Services and works are excluded by default, because their "quantity" counts
contracts rather than volume. As a secondary signal, an Isolation Forest is fitted across all series on
[ln ratio, ln amount ratio, ln unit-price ratio, number of plan points]. Its score is shown but never flags
anything on its own.

Limitation: the window is 2024–2026, so 2025 has a one-year baseline. Planned volumes can differ from what
was actually executed.

## 4. Data quality → confidence

DQ rules live in `tenderai/etl/dq.sql`. `error` rows are excluded from statistics; `warning` rows are kept.
The issue rate of the entity in question is reported in every answer and feeds the confidence score.

Fair Price has its own confidence (section 1, step 6). Every other answer (search, comparisons, price statistics,
anomalies, volumes) carries an aggregate confidence:

  confidence = 0.6 · min(1, ln N / ln 30) + 0.4 · (1 − DQ exclusion rate)

labelled high ≥ 0.70, medium ≥ 0.45, low otherwise; fewer than 5 objects is always low.

## 4a. Fairness of the quantity of a lot

The fairness answer for a lot also checks its **quantity**: the customer's planned volume of that ENSTRU × unit in
the lot's year is compared with the median of the customer's earlier years (`mart.volume_yearly`). Growth above
1.5× is reported as atypical, the same rule as the volume anomalies in section 3. Without an earlier year the
answer says that there is nothing to compare with.

## 5. Explainability contract

Every answer has six sections, rendered from the tool result and not by the LLM:

1. **Verdict.** 1–3 sentences: LLM-written and fact-validated, or a template fallback.
2. **Search parameters and data used.** Period, BINs, filters, how the item was resolved, N.
3. **Analytics and comparison.** Baselines, weighted averages and medians, deviations.
4. **Metric.** The method in one paragraph.
5. **Confidence and limitations.** Score and label, DQ rates, applicable limitations.
6. **Top-K.** Object IDs with direct goszakup.gov.kz links. Table-style answers (comparisons, price statistics)
   list the largest purchases behind the table.
