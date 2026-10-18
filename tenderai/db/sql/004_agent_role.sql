-- Read-only role for the agent's tools. The dev password is overridden in production via
-- `ALTER ROLE tenderai_agent PASSWORD ...` from a secret (see deploy/k8s/README.md).
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tenderai_agent') THEN
    CREATE ROLE tenderai_agent LOGIN PASSWORD 'tenderai_agent';
  END IF;
END $$;
GRANT USAGE ON SCHEMA core, mart TO tenderai_agent;
GRANT SELECT ON ALL TABLES IN SCHEMA core, mart TO tenderai_agent;
ALTER DEFAULT PRIVILEGES IN SCHEMA core, mart GRANT SELECT ON TABLES TO tenderai_agent;
ALTER ROLE tenderai_agent SET statement_timeout = '15s';
ALTER ROLE tenderai_agent SET default_transaction_read_only = on;
