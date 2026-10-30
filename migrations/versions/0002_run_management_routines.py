"""PostgreSQL routines for inspecting and removing ingestion runs."""
from alembic import op


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE OR REPLACE PROCEDURE public.delete_ingestion_run(p_run_id VARCHAR(36))
        LANGUAGE plpgsql
        AS $procedure$
        DECLARE
            selected_run public.ingestion_runs%ROWTYPE;
        BEGIN
            SELECT *
            INTO selected_run
            FROM public.ingestion_runs
            WHERE id = p_run_id
            FOR UPDATE;

            IF NOT FOUND THEN
                RAISE EXCEPTION 'Ingestion run not found: %', p_run_id
                    USING ERRCODE = 'P0002';
            END IF;

            -- These tables have a direct FK to ingestion_runs.
            DELETE FROM public.data_revisions
            WHERE run_id = p_run_id;

            DELETE FROM public.data_quality_issues
            WHERE run_id = p_run_id;

            DELETE FROM public.corporate_actions
            WHERE run_id = p_run_id;

            DELETE FROM public.market_data
            WHERE run_id = p_run_id;

            -- Coverage has no run_id. Invalidate overlapping coverage for the
            -- affected series so a later ingestion cannot skip the deleted range.
            DELETE FROM public.ingestion_coverage
            WHERE instrument_id = selected_run.instrument_id
              AND provider = selected_run.provider
              AND feed = selected_run.feed
              AND "interval" = selected_run."interval"
              AND adjustment = selected_run.adjustment
              AND session = selected_run.session
              AND "end" > selected_run.start
              AND start < selected_run."end";

            -- Do not move a cursor backwards if a newer run has already advanced it.
            DELETE FROM public.ingestion_state
            WHERE instrument_id = selected_run.instrument_id
              AND provider = selected_run.provider
              AND feed = selected_run.feed
              AND "interval" = selected_run."interval"
              AND adjustment = selected_run.adjustment
              AND session = selected_run.session
              AND cursor <= selected_run."end";

            DELETE FROM public.ingestion_runs
            WHERE id = p_run_id;

            RAISE NOTICE 'Ingestion run deleted: %', p_run_id;
        END;
        $procedure$;
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.list_ingestion_runs()
        RETURNS TABLE (
            run_id VARCHAR(36),
            market_data_records BIGINT,
            executed_on DATE,
            started_at TIMESTAMP WITH TIME ZONE,
            status VARCHAR(16),
            provider VARCHAR(32),
            feed VARCHAR(32),
            "interval" VARCHAR(8),
            adjustment VARCHAR(32),
            session VARCHAR(16)
        )
        LANGUAGE sql
        STABLE
        AS $function$
            SELECT
                r.id AS run_id,
                COUNT(m.timestamp)::BIGINT AS market_data_records,
                r.started_at::DATE AS executed_on,
                r.started_at,
                r.status,
                r.provider,
                r.feed,
                r."interval",
                r.adjustment,
                r.session
            FROM public.ingestion_runs AS r
            LEFT JOIN public.market_data AS m
                ON m.run_id = r.id
            GROUP BY
                r.id,
                r.started_at,
                r.status,
                r.provider,
                r.feed,
                r."interval",
                r.adjustment,
                r.session
            ORDER BY r.started_at DESC, r.id;
        $function$;
        """
    )


def downgrade():
    op.execute("DROP FUNCTION IF EXISTS public.list_ingestion_runs()")
    op.execute("DROP PROCEDURE IF EXISTS public.delete_ingestion_run(VARCHAR)")
