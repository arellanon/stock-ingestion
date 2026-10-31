"""Expose session_date as a PostgREST computed field."""

from collections.abc import Sequence

from alembic import op


revision: str = "0008_postgrest_session_date"
down_revision: str | None = "0007_market_data_view"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.session_date(public.market_data)
        RETURNS date
        LANGUAGE sql
        STABLE
        AS $function$
            SELECT ($1.timestamp AT TIME ZONE current_setting('TIMEZONE'))::date;
        $function$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE VIEW public.vw_market_data AS
        SELECT
            i.ticker,
            public.session_date(m) AS session_date,
            public.session_time(m) AS session_time,
            m.open,
            m.high,
            m.low,
            m.close,
            m.volume,
            m.complete
        FROM public.market_data AS m
        JOIN public.instruments AS i
          ON i.id = m.instrument_id;
        """
    )
    op.execute("NOTIFY pgrst, 'reload schema'")


def downgrade() -> None:
    op.execute(
        """
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
        JOIN public.instruments AS i
          ON i.id = m.instrument_id;
        """
    )
    op.execute("DROP FUNCTION IF EXISTS public.session_date(public.market_data)")
    op.execute("NOTIFY pgrst, 'reload schema'")
