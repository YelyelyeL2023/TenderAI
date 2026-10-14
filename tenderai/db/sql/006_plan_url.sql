-- Portal link for plan points: /ru/registry/show_plan/{plan_point_id}/{active plan act id}
ALTER TABLE core.plan_point ADD COLUMN IF NOT EXISTS url text;
