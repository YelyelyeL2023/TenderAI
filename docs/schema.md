# Storage schema (PostgreSQL 16)

Four schemas, each with one job:

| Schema | Purpose | Written by |
|---|---|---|
| `raw` | Exact API payloads (JSONB), idempotent upserts keyed by `(entity, id)` with an md5 change hash and a soft-delete flag | `tenderai/ingest` |
| `core` | Typed, cleaned, linked entities; rebuilt deterministically from `raw` in one transaction | `tenderai/etl/transform.sql`, `dq.sql` |
| `mart` | Analytical tables the agent reads (prices, deviations, volumes, statistics, DQ summary) | `tenderai/etl/marts.sql` |
| `ops` | Ingestion runs, per-BIN reconciliation (API `totalCount` vs fetched), watermarks | ingest |

Keeping raw payloads means cleaning rules can be changed and core rebuilt (about 1 minute) without calling the API
again. Every number in an answer can be traced back to the payload it came from.

## ER diagram (core)

```mermaid
erDiagram
    target_customer ||--o{ plan_point : "customer_bin"
    subject ||--o{ plan_point : "customer_bin"
    subject ||--o{ contract : "supplier_biin"
    plan_point ||--o{ lot : "plan_point_id"
    announcement ||--o{ lot : "announcement_id"
    lot ||--o{ contract_unit : "lot_id"
    plan_point ||--o{ contract_unit : "plan_point_id"
    contract ||--|{ contract_unit : "contract_id"
    announcement ||--o{ contract : "announcement_id"
    ref_unit ||--o{ plan_point : "unit_code"
    ref_enstru ||--o{ plan_point : "enstru_code"
    ref_kato ||--o{ plan_point : "kato_code"
    dq_issue }o--|| plan_point : "entity/obj_id"

    plan_point {
        bigint id PK
        bigint rootrecord_id
        bool is_latest
        text customer_bin
        int year
        text enstru_code
        text name_clean
        text unit_code
        text base_unit
        numeric count_base
        numeric price_base
        text kato_code
        text kato_source
    }
    lot {
        bigint id PK
        text lot_number
        bigint announcement_id FK
        bigint plan_point_id FK
        text customer_bin
        text name_clean
        numeric unit_price_base
        numeric count_base
        bool in_scope
    }
    contract {
        bigint id PK
        bigint root_id
        bigint parent_id
        bool is_latest
        text customer_bin
        text supplier_biin
        numeric sum
        timestamp sign_date
    }
    contract_unit {
        bigint id PK
        bigint contract_id FK
        bigint lot_id FK
        bigint plan_point_id FK
        numeric item_price
        numeric unit_price_base
        numeric quantity_base
    }
    announcement {
        bigint id PK
        text number_anno
        text org_bin
        timestamp publish_date
        text url
    }
```

## Core tables

| Table | Grain | Key columns |
|---|---|---|
| `core.plan_point` | plan point version | `is_latest` (latest per `rootrecord_id`), `enstru_code`, `unit_code` → `base_unit`/`unit_factor`, `count_base`, `price_base`, `kato_code` + `kato_source` |
| `core.announcement` | announcement | `number_anno`, `org_bin`, `publish_date`, `status_id`, `url` |
| `core.lot` | lot | `plan_point_id` (from `pointList[0]`), `unit_price_base`, `in_scope` (plan year or publish year in 2024–2026), `name_clean` |
| `core.contract` | contract version | `is_latest` (not superseded by a child), `sum` (without VAT), `sum_wnds`, `supplier_biin`, `url` |
| `core.contract_unit` | contract line | `item_price` (without VAT), `unit_price_base`, `quantity_base`, `lot_id`, `plan_point_id` |
| `core.subject` | customer or supplier | `biin`, `name_ru`, `kato_code`, `is_target` |
| `core.ref_unit` | МКЕИ unit | `base_unit`, `factor` (from `tenderai/etl/units.py`) |
| `core.ref_kato`, `core.ref_enstru`, `core.ref_status` | dictionaries | |
| `core.cpi` | month × region × category | official CPI levels (December 2020 = 100) and month-on-month change, from stat.gov.kz bulletins |
| `core.dq_issue` | (entity, obj_id, rule) | `severity` error / warning, `detail` JSONB |
| `core.target_customer` | BIN | the assignment's 27 customers |

## Marts

| Mart | Grain | Used by |
|---|---|---|
| `mart.price_obs` | one priced observation (contract line, or a lot without a contract) | all price tools, Fair Price candidates, search |
| `mart.price_stats` | ENSTRU × unit × (all / year) | `price_stats` |
| `mart.price_deviation` | observation + leave-one-out peer weighted average, robust z of log price | `price_anomalies` |
| `mart.volume_yearly` | customer × ENSTRU × unit × year (planned) | `volume_anomalies` |
| `mart.enstru_variant` | ENSTRU code × plan description (variant: "марка АИ-92", "летнее", "формат А4") | item resolution |
| `mart.dq_summary` | entity × rule | confidence blocks, monitoring |

## Access control

- `tenderai` owns everything and is used by ingest and ETL.
- `tenderai_agent` has **read-only** access to `core` and `mart` only (no `raw`, no `ops`), with
  `default_transaction_read_only = on` and `statement_timeout = 15s`. The agent's tools connect as this role.
