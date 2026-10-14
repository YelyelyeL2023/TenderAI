-- Where the delivery location came from: plan | lot | announcement | customer_address (inferred)
ALTER TABLE core.plan_point    ADD COLUMN IF NOT EXISTS kato_source text;
ALTER TABLE core.lot           ADD COLUMN IF NOT EXISTS kato_source text;
ALTER TABLE core.contract_unit ADD COLUMN IF NOT EXISTS kato_source text;
