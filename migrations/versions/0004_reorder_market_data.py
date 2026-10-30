"""Rebuild market_data so session_time follows session_date physically."""

from collections.abc import Sequence

from alembic import op


revision: str = "0004_reorder_market_data"
down_revision: str | None = "0003_session_time_delete_all_run"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE public.market_data_reordered (
            instrument_id VARCHAR(36) NOT NULL,
            provider VARCHAR(32) NOT NULL,
            feed VARCHAR(32) NOT NULL,
            "interval" VARCHAR(8) NOT NULL,
            adjustment VARCHAR(32) NOT NULL,
            session VARCHAR(16) NOT NULL,
            timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
            session_date DATE NOT NULL,
            session_time TIME WITHOUT TIME ZONE NOT NULL,
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
            PRIMARY KEY (instrument_id, provider, feed, "interval", adjustment, session, timestamp),
            FOREIGN KEY (provider, feed) REFERENCES public.provider_feeds (provider, name),
            FOREIGN KEY (instrument_id) REFERENCES public.instruments (id),
            FOREIGN KEY (provider) REFERENCES public.data_providers (name),
            FOREIGN KEY (run_id) REFERENCES public.ingestion_runs (id),
            CHECK (volume >= 0),
            CHECK ("low" > 0 AND "high" >= "low" AND "open" BETWEEN "low" AND "high" AND "close" BETWEEN "low" AND "high")
        );

        INSERT INTO public.market_data_reordered (
            instrument_id, provider, feed, "interval", adjustment, session,
            timestamp, session_date, session_time, open, high, low, close,
            volume, complete, obtained_at, updated_at, run_id, provenance
        )
        SELECT
            instrument_id, provider, feed, "interval", adjustment, session,
            timestamp, session_date, session_time, open, high, low, close,
            volume, complete, obtained_at, updated_at, run_id, provenance
        FROM public.market_data;

        DROP TABLE public.market_data;
        ALTER TABLE public.market_data_reordered RENAME TO market_data;

        ALTER INDEX public.market_data_reordered_pkey RENAME TO market_data_pkey;
        CREATE INDEX ix_market_instrument_timestamp
            ON public.market_data (instrument_id, timestamp);
        CREATE INDEX ix_market_timestamp
            ON public.market_data (timestamp);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        CREATE TABLE public.market_data_reordered (
            instrument_id VARCHAR(36) NOT NULL,
            provider VARCHAR(32) NOT NULL,
            feed VARCHAR(32) NOT NULL,
            "interval" VARCHAR(8) NOT NULL,
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
            session_time TIME WITHOUT TIME ZONE NOT NULL,
            PRIMARY KEY (instrument_id, provider, feed, "interval", adjustment, session, timestamp),
            FOREIGN KEY (provider, feed) REFERENCES public.provider_feeds (provider, name),
            FOREIGN KEY (instrument_id) REFERENCES public.instruments (id),
            FOREIGN KEY (provider) REFERENCES public.data_providers (name),
            FOREIGN KEY (run_id) REFERENCES public.ingestion_runs (id),
            CHECK (volume >= 0),
            CHECK ("low" > 0 AND "high" >= "low" AND "open" BETWEEN "low" AND "high" AND "close" BETWEEN "low" AND "high")
        );

        INSERT INTO public.market_data_reordered (
            instrument_id, provider, feed, "interval", adjustment, session,
            timestamp, session_date, open, high, low, close, volume, complete,
            obtained_at, updated_at, run_id, provenance, session_time
        )
        SELECT
            instrument_id, provider, feed, "interval", adjustment, session,
            timestamp, session_date, open, high, low, close, volume, complete,
            obtained_at, updated_at, run_id, provenance, session_time
        FROM public.market_data;

        DROP TABLE public.market_data;
        ALTER TABLE public.market_data_reordered RENAME TO market_data;

        ALTER INDEX public.market_data_reordered_pkey RENAME TO market_data_pkey;
        CREATE INDEX ix_market_instrument_timestamp
            ON public.market_data (instrument_id, timestamp);
        CREATE INDEX ix_market_timestamp
            ON public.market_data (timestamp);
        """
    )
