-- The assignment's customer list (filled from tenderai.config.TARGET_BINS by the ETL).
CREATE TABLE IF NOT EXISTS core.target_customer (bin text PRIMARY KEY);
GRANT SELECT ON core.target_customer TO tenderai_agent;
