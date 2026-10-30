"""Normalize session_time to UTC."""

from collections.abc import Sequence

from alembic import op


revision: str = "0005_session_time_utc"
down_revision: str | None = "0004_reorder_market_data"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE public.market_data
        SET session_time = (timestamp AT TIME ZONE 'UTC')::time
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE public.market_data
        SET session_time = (timestamp AT TIME ZONE 'America/New_York')::time
        """
    )
