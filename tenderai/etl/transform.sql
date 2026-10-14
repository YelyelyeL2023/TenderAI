-- Rebuild core.* from raw.* (idempotent; runs inside one transaction from tenderai.etl.run).
-- core.ref_unit is filled beforehand by Python (unit normalisation lives in tenderai/etl/units.py).

TRUNCATE core.ref_kato, core.ref_enstru, core.ref_status, core.subject, core.plan_point,
         core.announcement, core.lot, core.contract, core.contract_unit, core.dq_issue;

-- ---------- dictionaries ----------
INSERT INTO core.ref_kato (code, parent_code, name_ru, name_kz, full_name_ru, level, region_code)
SELECT payload->>'code', NULLIF(payload->>'parent_code', ''), payload->>'name_ru', payload->>'name_kz',
       payload->>'full_name_ru', (payload->>'level_')::int, left(payload->>'code', 2)
FROM raw.ref WHERE ref_name = 'ref_kato'
ON CONFLICT (code) DO NOTHING;

INSERT INTO core.ref_enstru (code, id, name_ru, name_kz, level, parent_id)
SELECT DISTINCT ON (e->>'code') e->>'code', (e->>'id')::bigint, e->>'nameRu', e->>'nameKz',
       (e->>'level')::int, (e->>'parentId')::bigint
FROM raw.ows_object, LATERAL (SELECT payload->'RefEnstru' AS e) x
WHERE entity = 'plan_point' AND jsonb_typeof(e) = 'object' AND coalesce(e->>'code', '') <> ''
ORDER BY e->>'code';

INSERT INTO core.ref_status (kind, id, code, name_ru, name_kz)
SELECT CASE ref_name
         WHEN 'ref_buy_status' THEN 'buy' WHEN 'ref_lots_status' THEN 'lot'
         WHEN 'ref_contract_status' THEN 'contract' WHEN 'ref_pln_point_status' THEN 'plan_point'
         WHEN 'ref_trade_methods' THEN 'trade_method' WHEN 'ref_subject_type' THEN 'subject_type'
         WHEN 'ref_months' THEN 'month' END,
       (payload->>'id')::int, coalesce(payload->>'code', payload->>'symbol_code'),
       payload->>'name_ru', payload->>'name_kz'
FROM raw.ref
WHERE ref_name IN ('ref_buy_status', 'ref_lots_status', 'ref_contract_status', 'ref_pln_point_status',
                   'ref_trade_methods', 'ref_subject_type', 'ref_months')
  AND payload->>'id' ~ '^[0-9]+$'
ON CONFLICT DO NOTHING;

-- ---------- subjects ----------
INSERT INTO core.subject (biin, pid, name_ru, name_kz, is_customer, is_supplier, type_supplier, kato_code, region_code)
SELECT DISTINCT ON (biin) biin, pid, name_ru, name_kz, is_customer, is_supplier, type_supplier, kato, left(kato, 2)
FROM (
    SELECT coalesce(NULLIF(payload->>'bin', ''), payload->>'iin') AS biin,
           (payload->>'pid')::bigint AS pid, payload->>'nameRu' AS name_ru, payload->>'nameKz' AS name_kz,
           (payload->>'customer')::int = 1 AS is_customer, (payload->>'supplier')::int = 1 AS is_supplier,
           (payload->>'typeSupplier')::int AS type_supplier,
           coalesce((SELECT a->>'katoCode' FROM jsonb_array_elements(CASE WHEN jsonb_typeof(payload->'Address') = 'array' THEN payload->'Address' ELSE '[]' END) a
                     WHERE coalesce(a->>'katoCode', '') <> '' ORDER BY (a->>'addressType') LIMIT 1),
                    NULLIF(payload->'katoList'->>0, '')) AS kato,
           fetched_at
    FROM raw.ows_object WHERE entity = 'subject' AND NOT is_deleted
) s
WHERE biin IS NOT NULL
ORDER BY biin, fetched_at DESC;

-- ---------- plan points ----------
INSERT INTO core.plan_point
SELECT p.id,
       (j->>'rootrecordId')::bigint,
       false,
       j->>'subjectBiin',
       (j->>'plnPointYear')::int,
       (j->>'refMonthsId')::int,
       NULLIF(j->>'refEnstruCode', ''),
       j->>'nameRu', NULL, j->>'descRu', j->>'extraDescRu',
       NULLIF(j->>'refUnitsCode', ''), u.base_unit, u.factor,
       (j->>'count')::numeric, (j->>'price')::numeric, (j->>'amount')::numeric,
       (j->>'count')::numeric * u.factor,
       CASE WHEN u.factor > 0 THEN (j->>'price')::numeric / u.factor END,
       (j->>'refTradeMethodsId')::int, (j->>'refSubjectTypeId')::int, (j->>'refPlnPointStatusId')::int,
       coalesce(k.kato, cs.kato_code), left(coalesce(k.kato, cs.kato_code), 2), k.addr,
       coalesce((j->>'isDeleted')::int, 0) = 1 OR p.is_deleted,
       NULLIF(j->>'dateCreate', '')::timestamp, NULLIF(j->>'timestamp', '')::timestamp,
       CASE WHEN k.kato IS NOT NULL THEN 'plan' WHEN cs.kato_code IS NOT NULL THEN 'customer_address' END,
       CASE WHEN coalesce(j->>'activeActId', j->>'createdinActId') ~ '^[1-9][0-9]*$'
            THEN 'https://goszakup.gov.kz/ru/registry/show_plan/' || p.id || '/' || coalesce(j->>'activeActId', j->>'createdinActId') END
FROM raw.ows_object p
CROSS JOIN LATERAL (SELECT p.payload AS j) jj
LEFT JOIN core.ref_unit u ON u.code = j->>'refUnitsCode'
LEFT JOIN core.subject cs ON cs.biin = j->>'subjectBiin'
LEFT JOIN LATERAL (
    SELECT NULLIF(pk->>'refKatoCode', '') AS kato, pk->>'fullDeliveryPlaceNameRu' AS addr
    FROM jsonb_array_elements(CASE WHEN jsonb_typeof(j->'PlansKato') = 'array' THEN j->'PlansKato' ELSE '[]' END) pk
    WHERE coalesce((pk->>'isDeleted')::int, 0) = 0 AND coalesce(pk->>'refKatoCode', '') <> ''
    ORDER BY (pk->>'count')::numeric DESC NULLS LAST LIMIT 1
) k ON true
WHERE p.entity = 'plan_point';

-- latest non-deleted version per rootrecord (plan amendments create new ids)
UPDATE core.plan_point pp SET is_latest = true
FROM (SELECT DISTINCT ON (coalesce(rootrecord_id, id)) id FROM core.plan_point
      WHERE NOT is_deleted ORDER BY coalesce(rootrecord_id, id), updated_at DESC NULLS LAST, id DESC) l
WHERE pp.id = l.id;

-- ---------- announcements ----------
INSERT INTO core.announcement
SELECT t.id, j->>'numberAnno', j->>'orgBin', NULLIF(j->>'customerBin', ''), j->>'nameRu',
       (j->>'totalSum')::numeric, (j->>'countLots')::int, (j->>'refTradeMethodsId')::int,
       (j->>'refSubjectTypeId')::int, (j->>'refBuyStatusId')::int,
       NULLIF(j->>'publishDate', '')::timestamp, NULLIF(j->>'endDate', '')::timestamp,
       NULLIF(j->>'itogiDatePublic', '')::timestamp,
       (j->'finYear'->>0)::int, NULLIF(j->'kato'->>0, ''),
       'https://goszakup.gov.kz/ru/announce/index/' || t.id
FROM raw.ows_object t CROSS JOIN LATERAL (SELECT t.payload AS j) jj
WHERE t.entity = 'trd_buy' AND NOT t.is_deleted;

-- ---------- lots ----------
INSERT INTO core.lot
SELECT l.id, j->>'lotNumber', (j->>'trdBuyId')::bigint, j->>'trdBuyNumberAnno', j->>'customerBin',
       j->>'nameRu', NULL, j->>'descriptionRu',
       (j->>'count')::numeric, (j->>'amount')::numeric,
       CASE WHEN (j->>'count')::numeric > 0 THEN (j->>'amount')::numeric / (j->>'count')::numeric END,
       pp.id, pp.enstru_code, pp.unit_code, pp.base_unit, pp.unit_factor,
       (j->>'count')::numeric * pp.unit_factor,
       CASE WHEN (j->>'count')::numeric > 0 AND pp.unit_factor > 0
            THEN (j->>'amount')::numeric / ((j->>'count')::numeric * pp.unit_factor) END,
       kato.code, left(kato.code, 2),
       (j->>'refLotStatusId')::int, (j->>'refTradeMethodsId')::int,
       a.publish_date,
       coalesce(pp.year, extract(year FROM a.publish_date)::int),
       coalesce(pp.year BETWEEN 2024 AND 2026, false) OR coalesce(extract(year FROM a.publish_date) BETWEEN 2024 AND 2026, false),
       coalesce((j->>'isDeleted')::int, 0) = 1 OR l.is_deleted,
       'https://goszakup.gov.kz/ru/announce/index/' || (j->>'trdBuyId'),
       kato.src
FROM raw.ows_object l
CROSS JOIN LATERAL (SELECT l.payload AS j) jj
LEFT JOIN core.plan_point pp ON pp.id = CASE WHEN j->'pointList'->>0 ~ '^[0-9]+$' THEN (j->'pointList'->>0)::bigint END
LEFT JOIN core.announcement a ON a.id = (j->>'trdBuyId')::bigint
CROSS JOIN LATERAL (
    SELECT CASE WHEN NULLIF(j->'plnPointKatoList'->>0, '') IS NOT NULL THEN j->'plnPointKatoList'->>0
                WHEN pp.kato_source = 'plan' THEN pp.kato_code
                WHEN a.kato_code IS NOT NULL THEN a.kato_code
                ELSE pp.kato_code END AS code,
           CASE WHEN NULLIF(j->'plnPointKatoList'->>0, '') IS NOT NULL THEN 'lot'
                WHEN pp.kato_source = 'plan' THEN 'plan'
                WHEN a.kato_code IS NOT NULL THEN 'announcement'
                ELSE pp.kato_source END AS src
) kato
WHERE l.entity = 'lot';

-- ---------- contracts ----------
INSERT INTO core.contract
SELECT c.id, NULLIF((j->>'rootId')::bigint, 0), NULLIF((j->>'parentId')::bigint, 0), false,
       j->>'contractNumber', j->>'contractNumberSys', NULLIF((j->>'trdBuyId')::bigint, 0), j->>'trdBuyNumberAnno',
       j->>'customerBin', NULLIF(j->>'supplierBiin', ''),
       (j->>'refContractStatusId')::int, (j->>'refContractTypeId')::int, (j->>'finYear')::int,
       coalesce(NULLIF(j->>'refCurrencyCode', ''), 'KZT'), (j->>'exchangeRate')::numeric,
       (j->>'contractSum')::numeric, (j->>'contractSumWnds')::numeric, (j->>'faktSumWnds')::numeric,
       NULLIF(j->>'signDate', '')::timestamp, (j->>'faktTradeMethodsId')::int, (j->>'refSubjectTypeId')::int,
       j->>'descriptionRu', coalesce((j->>'deleted')::int, 0) = 1 OR c.is_deleted,
       'https://goszakup.gov.kz/ru/egzcontract/cpublic/show/' || c.id
FROM raw.ows_object c CROSS JOIN LATERAL (SELECT c.payload AS j) jj
WHERE c.entity = 'contract';

-- final version of each amendment chain = not superseded by a non-deleted child
UPDATE core.contract c SET is_latest = true
WHERE NOT c.deleted
  AND NOT EXISTS (SELECT 1 FROM core.contract ch WHERE ch.parent_id = c.id AND NOT ch.deleted);

INSERT INTO core.contract_unit
SELECT DISTINCT ON ((u->>'id')::bigint)
       (u->>'id')::bigint, c.id, NULLIF((u->>'lotId')::bigint, 0), pp.id,
       coalesce(NULLIF(u->'refEnstruCode'->>0, ''), pp.enstru_code),
       pp.name_clean, pp.unit_code, pp.base_unit, pp.unit_factor,
       (u->>'quantity')::numeric, (u->>'quantity')::numeric * pp.unit_factor,
       (u->>'itemPrice')::numeric, (u->>'itemPriceWnds')::numeric,
       CASE WHEN pp.unit_factor > 0 THEN (u->>'itemPrice')::numeric / pp.unit_factor END,
       (u->>'totalSum')::numeric, (u->>'totalSumWnds')::numeric,
       coalesce(lt.kato_code, pp.kato_code), left(coalesce(lt.kato_code, pp.kato_code), 2),
       coalesce((u->>'deleted')::int, 0) = 1,
       coalesce(lt.kato_source, pp.kato_source)
FROM raw.ows_object c
CROSS JOIN LATERAL jsonb_array_elements(CASE WHEN jsonb_typeof(c.payload->'ContractUnits') = 'array'
                                             THEN c.payload->'ContractUnits' ELSE '[]' END) u
LEFT JOIN core.plan_point pp ON pp.id = NULLIF((u->>'plnPointId')::bigint, 0)
LEFT JOIN core.lot lt ON lt.id = NULLIF((u->>'lotId')::bigint, 0)
WHERE c.entity = 'contract'
ORDER BY (u->>'id')::bigint, c.id DESC;

UPDATE core.subject SET is_target = true
WHERE biin IN (SELECT bin FROM core.target_customer);

-- Contract lines whose plan point was deleted upstream still carry their ENSTRU code. Infer the unit from the
-- code only where it is unambiguous: >= 90% of at least 3 plan points with that code use the same unit.
WITH dominant AS (
    SELECT enstru_code, unit_code, base_unit, unit_factor
    FROM (SELECT enstru_code, unit_code, base_unit, unit_factor, count(*) n,
                 sum(count(*)) OVER (PARTITION BY enstru_code) total,
                 row_number() OVER (PARTITION BY enstru_code ORDER BY count(*) DESC) rn
          FROM core.plan_point WHERE enstru_code IS NOT NULL AND base_unit IS NOT NULL
          GROUP BY enstru_code, unit_code, base_unit, unit_factor) x
    WHERE rn = 1 AND total >= 3 AND n >= 0.9 * total
)
UPDATE core.contract_unit u
SET unit_code = d.unit_code, base_unit = d.base_unit, unit_factor = d.unit_factor,
    quantity_base = u.quantity * d.unit_factor,
    unit_price_base = CASE WHEN d.unit_factor > 0 THEN u.item_price / d.unit_factor END
FROM dominant d
WHERE u.plan_point_id IS NULL AND u.base_unit IS NULL AND u.enstru_code = d.enstru_code;

UPDATE core.contract_unit u SET name_clean = lower(e.name_ru)
FROM core.ref_enstru e WHERE u.name_clean IS NULL AND e.code = u.enstru_code;
