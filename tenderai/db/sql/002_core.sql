-- Core layer: typed, cleaned, linked entities. Rebuilt deterministically from raw by tenderai/etl/transform.sql.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ---------- dictionaries ----------
CREATE TABLE IF NOT EXISTS core.ref_unit (
    code        text PRIMARY KEY,
    name_ru     text,
    alpha_code  text,
    base_unit   text NOT NULL,      -- comparable base: kg, L, m, m2, pcs, service, or u<code> for packaging units
    factor      numeric NOT NULL    -- quantity_base = quantity * factor
);

CREATE TABLE IF NOT EXISTS core.ref_kato (
    code        text PRIMARY KEY,   -- 9-digit KATO
    parent_code text,
    name_ru     text,
    name_kz     text,
    full_name_ru text,
    level       int,
    region_code text                -- first 2 digits: oblast / city of republican significance
);

CREATE TABLE IF NOT EXISTS core.ref_enstru (
    code    text PRIMARY KEY,
    id      bigint,
    name_ru text,
    name_kz text,
    level   int,
    parent_id bigint
);

CREATE TABLE IF NOT EXISTS core.ref_status (
    kind    text NOT NULL,          -- buy | lot | contract | plan_point | trade_method | subject_type
    id      int  NOT NULL,
    code    text,
    name_ru text,
    name_kz text,
    PRIMARY KEY (kind, id)
);

-- Official CPI (optional seed, stat.gov.kz). Month = first day of month, index on any fixed base.
CREATE TABLE IF NOT EXISTS core.cpi_monthly (
    month  date PRIMARY KEY,
    index  numeric NOT NULL,
    source text NOT NULL
);

-- ---------- entities ----------
CREATE TABLE IF NOT EXISTS core.subject (
    biin          text PRIMARY KEY,   -- BIN for legal entities, IIN for individual entrepreneurs
    pid           bigint,
    name_ru       text,
    name_kz       text,
    is_customer   boolean,
    is_supplier   boolean,
    type_supplier int,
    kato_code     text,
    region_code   text,
    is_target     boolean NOT NULL DEFAULT false
);

CREATE TABLE IF NOT EXISTS core.plan_point (
    id              bigint PRIMARY KEY,
    rootrecord_id   bigint,
    is_latest       boolean NOT NULL,     -- latest non-deleted version within rootrecord
    customer_bin    text NOT NULL,
    year            int,
    month_id        int,
    enstru_code     text,
    name_ru         text,
    name_clean      text,
    desc_ru         text,
    extra_desc_ru   text,
    unit_code       text,
    base_unit       text,
    unit_factor     numeric,
    count           numeric,
    price           numeric,              -- planned unit price, KZT without VAT
    amount          numeric,
    count_base      numeric,
    price_base      numeric,
    trade_method_id int,
    subject_type_id int,                  -- 1 goods, 2 works, 3 services
    status_id       int,
    kato_code       text,
    region_code     text,
    delivery_address text,
    is_deleted      boolean,
    date_create     timestamp,
    updated_at      timestamp
);

CREATE TABLE IF NOT EXISTS core.announcement (
    id              bigint PRIMARY KEY,
    number_anno     text,
    org_bin         text,
    customer_bin    text,
    name_ru         text,
    total_sum       numeric,
    count_lots      int,
    trade_method_id int,
    subject_type_id int,
    status_id       int,
    publish_date    timestamp,
    end_date        timestamp,
    itogi_date      timestamp,
    fin_year        int,
    kato_code       text,
    url             text
);

CREATE TABLE IF NOT EXISTS core.lot (
    id              bigint PRIMARY KEY,
    lot_number      text,
    announcement_id bigint,
    number_anno     text,
    customer_bin    text NOT NULL,
    name_ru         text,
    name_clean      text,
    description_ru  text,
    count           numeric,
    amount          numeric,              -- KZT without VAT
    unit_price      numeric,
    plan_point_id   bigint,
    enstru_code     text,
    unit_code       text,
    base_unit       text,
    unit_factor     numeric,
    count_base      numeric,
    unit_price_base numeric,
    kato_code       text,
    region_code     text,
    status_id       int,
    trade_method_id int,
    publish_date    timestamp,
    year            int,
    in_scope        boolean NOT NULL,     -- belongs to 2024-2026 by plan year or announcement date
    is_deleted      boolean,
    url             text
);

CREATE TABLE IF NOT EXISTS core.contract (
    id              bigint PRIMARY KEY,
    root_id         bigint,
    parent_id       bigint,
    is_latest       boolean NOT NULL,     -- final version within its amendment chain
    number          text,
    number_sys      text,
    announcement_id bigint,
    number_anno     text,
    customer_bin    text NOT NULL,
    supplier_biin   text,
    status_id       int,
    type_id         int,
    fin_year        int,
    currency        text,
    exchange_rate   numeric,
    sum             numeric,              -- without VAT
    sum_wnds        numeric,              -- with VAT
    fact_sum_wnds   numeric,
    sign_date       timestamp,
    trade_method_id int,
    subject_type_id int,
    description_ru  text,
    deleted         boolean,
    url             text
);

CREATE TABLE IF NOT EXISTS core.contract_unit (
    id              bigint PRIMARY KEY,
    contract_id     bigint NOT NULL,
    lot_id          bigint,
    plan_point_id   bigint,
    enstru_code     text,
    name_clean      text,
    unit_code       text,
    base_unit       text,
    unit_factor     numeric,
    quantity        numeric,
    quantity_base   numeric,
    item_price      numeric,              -- without VAT
    item_price_wnds numeric,
    unit_price_base numeric,
    total_sum       numeric,
    total_sum_wnds  numeric,
    kato_code       text,
    region_code     text,
    deleted         boolean
);

CREATE TABLE IF NOT EXISTS core.dq_issue (
    entity      text NOT NULL,
    obj_id      bigint NOT NULL,
    rule        text NOT NULL,
    severity    text NOT NULL,            -- error (excluded from price stats) | warning
    detail      jsonb,
    detected_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (entity, obj_id, rule)
);
