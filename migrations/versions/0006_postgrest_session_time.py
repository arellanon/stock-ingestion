"""Replace the physical session_time column with a PostgREST computed field."""

from collections.abc import Sequence

from alembic import op


revision: str = "0006_postgrest_session_time"
down_revision: str | None = "0005_session_time_utc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE public.market_data DROP COLUMN session_time")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.session_time(public.market_data)
        RETURNS time
        LANGUAGE sql
        STABLE
        AS $function$
            SELECT ($1.timestamp AT TIME ZONE current_setting('TIMEZONE'))::time;
        $function$;
        """
    )
    op.execute("NOTIFY pgrst, 'reload schema'")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS public.session_time(public.market_data)")
    op.execute(
        """
        ALTER TABLE public.market_data
        ADD COLUMN session_time TIME WITHOUT TIME ZONE;

        UPDATE public.market_data
        SET session_time = (timestamp AT TIME ZONE 'UTC')::time;

        ALTER TABLE public.market_data
        ALTER COLUMN session_time SET NOT NULL;
        """
    )
