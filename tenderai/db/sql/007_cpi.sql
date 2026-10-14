-- Official CPI (stat.gov.kz bulletin T-15-02-M, sheets 1 and 2): fixed-base index (December 2020 = 100)
-- by month, region (2-digit KATO, 'KZ' = national) and category (total | food | nonfood | services).
CREATE TABLE IF NOT EXISTS core.cpi (
    month       date    NOT NULL,
    region_code text    NOT NULL,
    category    text    NOT NULL,
    index       numeric NOT NULL,     -- level, December 2020 = 100
    mom         numeric,              -- month-on-month, previous month = 100
    source      text    NOT NULL,
    PRIMARY KEY (month, region_code, category)
);
GRANT SELECT ON core.cpi TO tenderai_agent;
