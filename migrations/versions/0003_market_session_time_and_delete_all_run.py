"""Add market session time and an all-runs deletion procedure."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0003_session_time_delete_all_run"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("market_data", sa.Column("session_time", sa.Time(), nullable=True), schema="public")
    op.execute(
        """
        UPDATE public.market_data
        SET session_time = (timestamp AT TIME ZONE 'UTC')::time
        WHERE session_time IS NULL
        """
    )
    op.alter_column(
        "market_data", "session_time", existing_type=sa.Time(), nullable=False, schema="public"
    )
    op.execute(
        """
        CREATE OR REPLACE PROCEDURE public.delete_all_run()
        LANGUAGE plpgsql
        AS $procedure$
        BEGIN
            DELETE FROM public.data_revisions;
            DELETE FROM public.data_quality_issues;
            DELETE FROM public.corporate_actions;
            DELETE FROM public.market_data;
            DELETE FROM public.ingestion_coverage;
            DELETE FROM public.ingestion_state;
            DELETE FROM public.ingestion_runs;
        END;
        $procedure$;
        """
    )


def downgrade() -> None:
    op.execute("DROP PROCEDURE IF EXISTS public.delete_all_run()")
    op.drop_column("market_data", "session_time", schema="public")
