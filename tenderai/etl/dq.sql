-- Data-quality rules. severity 'error' => row excluded from price statistics; 'warning' => kept, lowers confidence.

-- plan points
INSERT INTO core.dq_issue (entity, obj_id, rule, severity, detail)
SELECT 'plan_point', id, 'non_positive_price_or_count', 'error', jsonb_build_object('price', price, 'count', count)
FROM core.plan_point WHERE NOT is_deleted AND (coalesce(price, 0) <= 0 OR coalesce(count, 0) <= 0)
UNION ALL
SELECT 'plan_point', id, 'amount_ne_count_x_price', 'warning',
       jsonb_build_object('amount', amount, 'count', count, 'price', price)
FROM core.plan_point WHERE NOT is_deleted AND price > 0 AND count > 0
  AND abs(amount - count * price) > greatest(1, 0.01 * amount)
UNION ALL
SELECT 'plan_point', id, 'missing_enstru', 'error', NULL FROM core.plan_point WHERE NOT is_deleted AND enstru_code IS NULL
UNION ALL
SELECT 'plan_point', id, 'unknown_unit', 'warning', jsonb_build_object('unit_code', unit_code)
FROM core.plan_point WHERE NOT is_deleted AND (unit_code IS NULL OR base_unit IS NULL)
UNION ALL
SELECT 'plan_point', id, 'delivery_kato_inferred_or_missing', 'warning', jsonb_build_object('kato_source', kato_source)
FROM core.plan_point WHERE NOT is_deleted AND kato_source IS DISTINCT FROM 'plan'
UNION ALL
SELECT 'plan_point', id, 'year_out_of_scope', 'warning', jsonb_build_object('year', year)
FROM core.plan_point WHERE year NOT BETWEEN 2024 AND 2026;

-- lots
INSERT INTO core.dq_issue (entity, obj_id, rule, severity, detail)
SELECT 'lot', id, 'non_positive_amount_or_count', 'error', jsonb_build_object('amount', amount, 'count', count)
FROM core.lot WHERE in_scope AND NOT is_deleted AND (coalesce(amount, 0) <= 0 OR coalesce(count, 0) <= 0)
UNION ALL
SELECT 'lot', id, 'no_plan_point_link', 'warning', NULL
FROM core.lot WHERE in_scope AND NOT is_deleted AND plan_point_id IS NULL
UNION ALL
SELECT 'lot', l.id, 'lot_price_differs_from_plan', 'warning',
       jsonb_build_object('lot_unit_price', round(l.unit_price, 2), 'plan_price', p.price)
FROM core.lot l JOIN core.plan_point p ON p.id = l.plan_point_id
WHERE l.in_scope AND NOT l.is_deleted AND p.price > 0 AND l.unit_price > 0
  AND abs(l.unit_price / p.price - 1) > 0.5;

-- contract line items
INSERT INTO core.dq_issue (entity, obj_id, rule, severity, detail)
SELECT 'contract_unit', u.id, 'non_positive_price_or_quantity', 'error',
       jsonb_build_object('item_price', item_price, 'quantity', quantity)
FROM core.contract_unit u WHERE NOT deleted AND (coalesce(item_price, 0) <= 0 OR coalesce(quantity, 0) <= 0)
UNION ALL
SELECT 'contract_unit', u.id, 'total_ne_price_x_quantity', 'warning',
       jsonb_build_object('total_sum', total_sum, 'item_price', item_price, 'quantity', quantity)
FROM core.contract_unit u WHERE NOT deleted AND item_price > 0 AND quantity > 0
  AND abs(total_sum - item_price * quantity) > greatest(1, 0.01 * total_sum)
UNION ALL
SELECT 'contract_unit', u.id, 'missing_unit_or_enstru', 'error', NULL
FROM core.contract_unit u WHERE NOT deleted AND (base_unit IS NULL OR enstru_code IS NULL);

-- contracts
INSERT INTO core.dq_issue (entity, obj_id, rule, severity, detail)
SELECT 'contract', id, 'non_kzt_currency', 'warning', jsonb_build_object('currency', currency)
FROM core.contract WHERE is_latest AND currency <> 'KZT'
UNION ALL
SELECT 'contract', id, 'sum_wnds_lt_sum', 'warning', jsonb_build_object('sum', sum, 'sum_wnds', sum_wnds)
FROM core.contract WHERE is_latest AND sum_wnds + 1 < sum
UNION ALL
SELECT 'contract', c.id, 'contract_exceeds_lot_amount', 'warning',
       jsonb_build_object('contract_sum', c.sum, 'lots_amount', s.amt)
FROM core.contract c
JOIN (SELECT u.contract_id, sum(l.amount) amt FROM core.contract_unit u JOIN core.lot l ON l.id = u.lot_id
      WHERE NOT u.deleted GROUP BY u.contract_id) s ON s.contract_id = c.id
WHERE c.is_latest AND c.sum > s.amt * 1.01;

INSERT INTO core.dq_issue (entity, obj_id, rule, severity, detail)
SELECT 'contract_unit', id, 'unit_inferred_from_enstru', 'warning', jsonb_build_object('unit_code', unit_code)
FROM core.contract_unit WHERE NOT deleted AND plan_point_id IS NULL AND base_unit IS NOT NULL;
