"""Create a compact market-data view for API and reporting queries."""

from collections.abc import Sequence

from alembic import op


revision: str = "0007_market_data_view"
down_revision: str | None = "0006_postgrest_session_time"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS public.vw_market_data")
