-- Analytical marts (rebuilt after each ETL run). Everything the agent's tools read lives here.

-- 1. Price observations: actual contract prices first; lots only when no contract line refers to them.
DROP TABLE IF EXISTS mart.price_obs CASCADE;
CREATE TABLE mart.price_obs AS
SELECT 'contract_unit:' || u.id          AS obj_id,
       'contract'                         AS source,
       u.id                               AS source_id,
       c.id                               AS contract_id,
       coalesce(c.announcement_id, l.announcement_id) AS announcement_id,
       u.lot_id,
       c.customer_bin,
       c.supplier_biin,
       u.enstru_code,
       coalesce(u.name_clean, l.name_clean) AS name_clean,
       u.base_unit,
       u.unit_price_base                  AS price,          -- KZT without VAT per base unit
       u.quantity_base                    AS quantity,
       u.unit_price_base * u.quantity_base AS amount,
       date_trunc('month', c.sign_date)::date AS month,
       extract(year FROM c.sign_date)::int AS year,
       u.kato_code                        AS city_kato,
       u.region_code,
       c.url
FROM core.contract_unit u
JOIN core.contract c ON c.id = u.contract_id AND c.is_latest
LEFT JOIN core.lot l ON l.id = u.lot_id
WHERE NOT u.deleted AND c.currency = 'KZT'
  AND u.unit_price_base > 0 AND u.quantity_base > 0 AND u.enstru_code IS NOT NULL
  AND c.sign_date >= '2024-01-01' AND c.sign_date < '2027-01-01'
  AND NOT EXISTS (SELECT 1 FROM core.dq_issue d WHERE d.entity = 'contract_unit' AND d.obj_id = u.id AND d.severity = 'error')
UNION ALL
SELECT 'lot:' || l.id, 'lot', l.id, NULL, l.announcement_id, l.id, l.customer_bin, NULL,
       l.enstru_code, l.name_clean, l.base_unit, l.unit_price_base, l.count_base, l.unit_price_base * l.count_base,
       date_trunc('month', coalesce(l.publish_date, make_date(l.year, 1, 1)))::date, l.year,
       l.kato_code, l.region_code, l.url
FROM core.lot l
WHERE l.in_scope AND NOT l.is_deleted
  AND l.unit_price_base > 0 AND l.count_base > 0 AND l.enstru_code IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM core.contract_unit u WHERE u.lot_id = l.id AND NOT u.deleted)
  AND NOT EXISTS (SELECT 1 FROM core.dq_issue d WHERE d.entity = 'lot' AND d.obj_id = l.id AND d.severity = 'error')
  -- a failed tender is re-announced as a new lot for the same plan point: keep only the latest attempt
  AND NOT EXISTS (SELECT 1 FROM core.lot l2
                  WHERE l2.plan_point_id = l.plan_point_id AND l2.in_scope AND NOT l2.is_deleted
                    AND (l2.publish_date, l2.id) > (l.publish_date, l.id));

-- Constant prices: every observation re-expressed in tenge of the latest CPI month, deflated with the official CPI
-- of its own region (national if the region is unknown) for its category: price * I_r(latest) / I_r(month). Same mapping as
-- tenderai.analytics.price_index.cpi_category. Without CPI data price_real = price.
ALTER TABLE mart.price_obs ADD COLUMN cpi_category text, ADD COLUMN cpi_month date,
                            ADD COLUMN price_real numeric, ADD COLUMN amount_real numeric;
UPDATE mart.price_obs SET
    cpi_category = CASE
        WHEN base_unit = 'work' OR left(enstru_code, 2) IN ('41', '42', '43') THEN 'total'
        WHEN left(enstru_code, 2) IN ('01', '02', '03', '10', '11') THEN 'food'
        WHEN base_unit = 'service' OR (left(enstru_code, 2) ~ '^[0-9]{2}$' AND left(enstru_code, 2)::int >= 35) THEN 'services'
        ELSE 'nonfood' END,
    -- months outside the CPI coverage use the nearest covered month
    cpi_month = least(greatest(month, (SELECT min(month) FROM core.cpi)), (SELECT max(month) FROM core.cpi));
-- plain equality joins (hashable): own region first, national fallback
UPDATE mart.price_obs o
SET price_real = o.price * b.index / c.index, amount_real = o.amount * b.index / c.index
FROM core.cpi c, core.cpi b
WHERE c.month = o.cpi_month AND c.region_code = o.region_code AND c.category = o.cpi_category
  -- same region at both ends: regional levels on the Dec-2020 base differ, so mixing them would bias prices
  AND b.month = (SELECT max(month) FROM core.cpi) AND b.region_code = o.region_code AND b.category = o.cpi_category;
UPDATE mart.price_obs o
SET price_real = o.price * b.index / c.index, amount_real = o.amount * b.index / c.index
FROM core.cpi c, core.cpi b
WHERE o.price_real IS NULL
  AND c.month = o.cpi_month AND c.region_code = 'KZ' AND c.category = o.cpi_category
  AND b.month = (SELECT max(month) FROM core.cpi) AND b.region_code = 'KZ' AND b.category = o.cpi_category;
UPDATE mart.price_obs SET price_real = price, amount_real = amount WHERE price_real IS NULL;

CREATE INDEX ON mart.price_obs (enstru_code, base_unit);
CREATE INDEX ON mart.price_obs (customer_bin);
CREATE INDEX ON mart.price_obs (lot_id);
CREATE INDEX ON mart.price_obs USING gin (name_clean gin_trgm_ops);

-- 2. Price statistics per ENSTRU x unit (all years, and per year)
DROP TABLE IF EXISTS mart.price_stats CASCADE;
CREATE TABLE mart.price_stats AS
SELECT enstru_code, base_unit, coalesce(year::text, 'all') AS period,
       count(*)                                    AS n,
       count(DISTINCT customer_bin)                AS n_customers,
       sum(amount) / nullif(sum(quantity), 0)      AS weighted_avg,
       percentile_cont(0.5)  WITHIN GROUP (ORDER BY price) AS median,
       percentile_cont(0.25) WITHIN GROUP (ORDER BY price) AS q1,
       percentile_cont(0.75) WITHIN GROUP (ORDER BY price) AS q3,
       min(price) AS min_price, max(price) AS max_price
FROM mart.price_obs
GROUP BY GROUPING SETS ((enstru_code, base_unit), (enstru_code, base_unit, year));
CREATE INDEX ON mart.price_stats (enstru_code, base_unit, period);

-- 3. Deviation of each observation from the quantity-weighted average price of the OTHER observations of the
--    same ENSTRU x unit (leave-one-out), computed in constant prices (official CPI), then expressed back in the
--    observation's own month so the displayed peer average is directly comparable with its nominal price.
--    Plus a robust z-score of the log constant price within the group (median / MAD).
DROP TABLE IF EXISTS mart.price_deviation CASCADE;
CREATE TABLE mart.price_deviation AS
WITH g AS MATERIALIZED (
    SELECT enstru_code, base_unit, count(*) n, sum(amount_real) s_amt, sum(quantity) s_qty,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY ln(price_real)) AS med_log
    FROM mart.price_obs GROUP BY 1, 2 HAVING count(*) >= 4   -- at least 3 peers
), g2 AS MATERIALIZED (   -- MAD as a second grouped aggregate (no per-row correlated subquery)
    SELECT g.*, percentile_cont(0.5) WITHIN GROUP (ORDER BY abs(ln(o.price_real) - g.med_log)) AS mad_log
    FROM g JOIN mart.price_obs o USING (enstru_code, base_unit)
    GROUP BY g.enstru_code, g.base_unit, g.n, g.s_amt, g.s_qty, g.med_log
), d AS (
    SELECT o.*, g2.n - 1 AS n_peers, g2.med_log, g2.mad_log,
           (g2.s_amt - o.amount_real) / nullif(g2.s_qty - o.quantity, 0) AS peer_avg_real,
           o.price / o.price_real AS to_nominal
    FROM mart.price_obs o JOIN g2 USING (enstru_code, base_unit)
)
SELECT d.obj_id, d.source, d.source_id, d.contract_id, d.announcement_id, d.lot_id, d.customer_bin, d.supplier_biin,
       d.enstru_code, d.name_clean, d.base_unit, d.price, d.quantity, d.amount, d.month, d.year, d.city_kato,
       d.region_code, d.url, d.cpi_category, d.price_real, d.n_peers,
       d.peer_avg_real * d.to_nominal AS peer_weighted_avg,          -- in the observation's own month prices
       d.price_real / nullif(d.peer_avg_real, 0) - 1 AS deviation,
       exp(d.med_log) * d.to_nominal AS group_median,
       CASE WHEN d.mad_log > 0 THEN (ln(d.price_real) - d.med_log) / (1.4826 * d.mad_log) END AS robust_z_log,
       d.base_unit IN ('service', 'work') AS is_service,
       -- > 21x the peer price is almost always a lump-sum lot entered as quantity 1 or a unit mix-up,
       -- not a price: reported separately and excluded from overpayment estimates
       d.price_real > 21 * d.peer_avg_real AS likely_unit_error
FROM d;
CREATE INDEX ON mart.price_deviation (deviation);
CREATE INDEX ON mart.price_deviation (enstru_code, base_unit);
CREATE INDEX ON mart.price_deviation (customer_bin);

-- 4. Planned yearly volumes per customer x ENSTRU x unit (latest version of each plan point)
DROP TABLE IF EXISTS mart.volume_yearly CASCADE;
CREATE TABLE mart.volume_yearly AS
SELECT p.customer_bin, p.enstru_code, p.base_unit, p.year,
       min(p.name_clean)          AS name_clean,
       sum(p.count_base)          AS quantity,
       sum(p.amount)              AS amount,
       count(*)                   AS n_points,
       array_agg(p.id ORDER BY p.amount DESC) AS plan_point_ids
FROM core.plan_point p
JOIN core.target_customer tc ON tc.bin = p.customer_bin
WHERE p.is_latest AND NOT p.is_deleted AND p.year BETWEEN 2024 AND 2026
  AND p.count_base > 0 AND p.enstru_code IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM core.dq_issue d WHERE d.entity = 'plan_point' AND d.obj_id = p.id AND d.severity = 'error')
GROUP BY 1, 2, 3, 4;
CREATE INDEX ON mart.volume_yearly (customer_bin, enstru_code, base_unit);

-- 5. Product variants. One product is often split into ENSTRU codes that share a name and differ only in the
--    characteristic recorded in the plan point description: petrol "марка АИ-92" / "марка АИ-95", diesel "летнее" /
--    "зимнее", office paper "формат А4" / "формат А3". Each code's descriptions (with their frequency) let the agent
--    tell variants apart; every plan point version counts, since the description belongs to the code.
DROP TABLE IF EXISTS mart.enstru_variant CASCADE;
CREATE TABLE mart.enstru_variant AS
SELECT enstru_code, btrim(desc_ru) AS variant, count(*) AS n
FROM core.plan_point
WHERE enstru_code IS NOT NULL AND nullif(btrim(desc_ru), '') IS NOT NULL
GROUP BY 1, 2;
CREATE INDEX ON mart.enstru_variant (enstru_code);

-- 6. Data-quality summary used in every answer's "confidence" block
DROP TABLE IF EXISTS mart.dq_summary CASCADE;
CREATE TABLE mart.dq_summary AS
SELECT entity, rule, severity, count(*) AS n FROM core.dq_issue GROUP BY 1, 2, 3;
