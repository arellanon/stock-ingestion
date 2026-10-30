BEGIN;

CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL, 
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

-- Running upgrade  -> 0001

CREATE TABLE data_providers (
    name VARCHAR(32) NOT NULL, 
    PRIMARY KEY (name)
);

CREATE TABLE instruments (
    id VARCHAR(36) NOT NULL, 
    ticker VARCHAR(16) NOT NULL, 
    enabled BOOLEAN NOT NULL, 
    metadata JSON NOT NULL, 
    PRIMARY KEY (id)
);

CREATE TABLE provider_feeds (
    provider VARCHAR(32) NOT NULL, 
    name VARCHAR(32) NOT NULL, 
    PRIMARY KEY (provider, name), 
    FOREIGN KEY(provider) REFERENCES data_providers (name)
);

CREATE TABLE instrument_identifiers (
    id VARCHAR(36) NOT NULL, 
    instrument_id VARCHAR(36) NOT NULL, 
    provider VARCHAR(32) NOT NULL, 
    symbol VARCHAR(32) NOT NULL, 
    external_id VARCHAR(128) NOT NULL, 
    valid_from DATE NOT NULL, 
    valid_to DATE, 
    PRIMARY KEY (id), 
    FOREIGN KEY(instrument_id) REFERENCES instruments (id), 
    FOREIGN KEY(provider) REFERENCES data_providers (name)
);

CREATE UNIQUE INDEX uq_active_identifier ON instrument_identifiers (provider, symbol) WHERE valid_to IS NULL;

CREATE TABLE ingestion_runs (
    id VARCHAR(36) NOT NULL, 
    instrument_id VARCHAR(36) NOT NULL, 
    provider VARCHAR(32) NOT NULL, 
    feed VARCHAR(32) NOT NULL, 
    interval VARCHAR(8) NOT NULL, 
    adjustment VARCHAR(32) NOT NULL, 
    session VARCHAR(16) NOT NULL, 
    start TIMESTAMP WITH TIME ZONE NOT NULL, 
    "end" TIMESTAMP WITH TIME ZONE NOT NULL, 
    started_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    finished_at TIMESTAMP WITH TIME ZONE, 
    received_start TIMESTAMP WITH TIME ZONE, 
    received_end TIMESTAMP WITH TIME ZONE, 
    status VARCHAR(16) NOT NULL, 
    count INTEGER NOT NULL, 
    error TEXT NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(provider, feed) REFERENCES provider_feeds (provider, name), 
    FOREIGN KEY(instrument_id) REFERENCES instruments (id), 
    FOREIGN KEY(provider) REFERENCES data_providers (name)
);

CREATE TABLE ingestion_state (
    instrument_id VARCHAR(36) NOT NULL, 
    provider VARCHAR(32) NOT NULL, 
    feed VARCHAR(32) NOT NULL, 
    interval VARCHAR(8) NOT NULL, 
    adjustment VARCHAR(32) NOT NULL, 
    session VARCHAR(16) NOT NULL, 
    cursor TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (instrument_id, provider, feed, interval, adjustment, session), 
    FOREIGN KEY(provider, feed) REFERENCES provider_feeds (provider, name), 
    FOREIGN KEY(instrument_id) REFERENCES instruments (id), 
    FOREIGN KEY(provider) REFERENCES data_providers (name)
);

CREATE TABLE ingestion_coverage (
    instrument_id VARCHAR(36) NOT NULL, 
    provider VARCHAR(32) NOT NULL, 
    feed VARCHAR(32) NOT NULL, 
    interval VARCHAR(8) NOT NULL, 
    adjustment VARCHAR(32) NOT NULL, 
    session VARCHAR(16) NOT NULL, 
    start TIMESTAMP WITH TIME ZONE NOT NULL, 
    "end" TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (instrument_id, provider, feed, interval, adjustment, session, start, "end"), 
    FOREIGN KEY(provider, feed) REFERENCES provider_feeds (provider, name), 
    FOREIGN KEY(instrument_id) REFERENCES instruments (id), 
    FOREIGN KEY(provider) REFERENCES data_providers (name)
);

CREATE TABLE market_data (
    instrument_id VARCHAR(36) NOT NULL, 
    provider VARCHAR(32) NOT NULL, 
    feed VARCHAR(32) NOT NULL, 
    interval VARCHAR(8) NOT NULL, 
    adjustment VARCHAR(32) NOT NULL, 
    session VARCHAR(16) NOT NULL, 
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL, 
    session_date DATE NOT NULL, 
    open NUMERIC(24, 10) NOT NULL, 
    high NUMERIC(24, 10) NOT NULL, 
    low NUMERIC(24, 10) NOT NULL, 
    close NUMERIC(24, 10) NOT NULL, 
    volume BIGINT NOT NULL, 
    complete BOOLEAN NOT NULL, 
    obtained_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    run_id VARCHAR(36) NOT NULL, 
    provenance JSON NOT NULL, 
    PRIMARY KEY (instrument_id, provider, feed, interval, adjustment, session, timestamp), 
    FOREIGN KEY(provider, feed) REFERENCES provider_feeds (provider, name), 
    CHECK (volume >= 0), 
    CHECK ("low" > 0 AND "high" >= "low" AND "open" BETWEEN "low" AND "high" AND "close" BETWEEN "low" AND "high"), 
    FOREIGN KEY(instrument_id) REFERENCES instruments (id), 
    FOREIGN KEY(provider) REFERENCES data_providers (name), 
    FOREIGN KEY(run_id) REFERENCES ingestion_runs (id)
);

CREATE INDEX ix_market_instrument_timestamp ON market_data (instrument_id, timestamp);

CREATE INDEX ix_market_timestamp ON market_data (timestamp);

CREATE OR REPLACE VIEW public.vw_market_data AS
SELECT
    i.ticker,
    m.session_date,
    public.session_time(m) AS session_time,
    m.open,
    m.high,
    m.low,
    m.close,
    m.volume,
    m.complete
FROM public.market_data AS m
JOIN public.instruments AS i ON i.id = m.instrument_id;

CREATE TABLE corporate_actions (
    instrument_id VARCHAR(36) NOT NULL, 
    provider VARCHAR(32) NOT NULL, 
    kind VARCHAR(32) NOT NULL, 
    ex_date DATE NOT NULL, 
    external_id VARCHAR(128) NOT NULL, 
    value NUMERIC(24, 10) NOT NULL, 
    metadata JSON NOT NULL, 
    run_id VARCHAR(36) NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (instrument_id, provider, kind, ex_date, external_id), 
    FOREIGN KEY(instrument_id) REFERENCES instruments (id), 
    FOREIGN KEY(provider) REFERENCES data_providers (name), 
    FOREIGN KEY(run_id) REFERENCES ingestion_runs (id)
);

CREATE TABLE data_quality_issues (
    id VARCHAR(36) NOT NULL, 
    instrument_id VARCHAR(36) NOT NULL, 
    provider VARCHAR(32) NOT NULL, 
    feed VARCHAR(32) NOT NULL, 
    interval VARCHAR(8) NOT NULL, 
    adjustment VARCHAR(32) NOT NULL, 
    session VARCHAR(16) NOT NULL, 
    run_id VARCHAR(36) NOT NULL, 
    code VARCHAR(32) NOT NULL, 
    message TEXT NOT NULL, 
    timestamp TIMESTAMP WITH TIME ZONE, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(provider, feed) REFERENCES provider_feeds (provider, name), 
    FOREIGN KEY(instrument_id) REFERENCES instruments (id), 
    FOREIGN KEY(provider) REFERENCES data_providers (name), 
    FOREIGN KEY(run_id) REFERENCES ingestion_runs (id)
);

CREATE TABLE data_revisions (
    id VARCHAR(36) NOT NULL, 
    entity VARCHAR(32) NOT NULL, 
    identity JSON NOT NULL, 
    previous JSON NOT NULL, 
    current JSON NOT NULL, 
    run_id VARCHAR(36) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(run_id) REFERENCES ingestion_runs (id)
);

INSERT INTO alembic_version (version_num) VALUES ('0001') RETURNING alembic_version.version_num;

COMMIT;

