"""Portable SQLAlchemy schema; PostgreSQL-specific writes live in persistence.py."""
import sqlalchemy as sa
metadata = sa.MetaData()

def table(name, *columns, **kwargs): return sa.Table(name, metadata, *columns, **kwargs)
def col(name, type_, **kwargs): return sa.Column(name, type_, **kwargs)
def dims():
    return [sa.Column("instrument_id",sa.String(36),sa.ForeignKey("instruments.id"),nullable=False),
            sa.Column("provider",sa.String(32),sa.ForeignKey("data_providers.name"),nullable=False),
            col("feed",sa.String(32),nullable=False),col("interval",sa.String(8),nullable=False),
            col("adjustment",sa.String(32),nullable=False),col("session",sa.String(16),nullable=False)]
DIM = ["instrument_id","provider","feed","interval","adjustment","session"]
def feed_fk(): return sa.ForeignKeyConstraint(["provider","feed"],["provider_feeds.provider","provider_feeds.name"])
providers = table("data_providers", col("name",sa.String(32),primary_key=True))
feeds = table("provider_feeds",sa.Column("provider",sa.String(32),sa.ForeignKey("data_providers.name"),primary_key=True),col("name",sa.String(32),primary_key=True))
instruments = table("instruments",col("id",sa.String(36),primary_key=True),col("ticker",sa.String(16),nullable=False),col("enabled",sa.Boolean,nullable=False),col("metadata",sa.JSON,nullable=False))
identifiers = table("instrument_identifiers",col("id",sa.String(36),primary_key=True),
    sa.Column("instrument_id",sa.String(36),sa.ForeignKey("instruments.id"),nullable=False),
    sa.Column("provider",sa.String(32),sa.ForeignKey("data_providers.name"),nullable=False),
    col("symbol",sa.String(32),nullable=False),col("external_id",sa.String(128),nullable=False),
    col("valid_from",sa.Date,nullable=False),col("valid_to",sa.Date))
sa.Index("uq_active_identifier", identifiers.c.provider, identifiers.c.symbol, unique=True, postgresql_where=identifiers.c.valid_to.is_(None), sqlite_where=identifiers.c.valid_to.is_(None))
runs = table("ingestion_runs",col("id",sa.String(36),primary_key=True),*dims(),
    col("start",sa.DateTime(timezone=True),nullable=False),col("end",sa.DateTime(timezone=True),nullable=False),
    col("started_at",sa.DateTime(timezone=True),nullable=False),col("finished_at",sa.DateTime(timezone=True)),
    col("received_start",sa.DateTime(timezone=True)),col("received_end",sa.DateTime(timezone=True)),
    col("status",sa.String(16),nullable=False),col("count",sa.Integer,nullable=False),col("error",sa.Text,nullable=False),feed_fk())
bars = table("market_data",*dims(),col("timestamp",sa.DateTime(timezone=True),nullable=False),
    col("session_date",sa.Date,nullable=False),*[col(k,sa.Numeric(24,10),nullable=False) for k in ("open","high","low","close")],
    col("volume",sa.BigInteger,nullable=False),col("complete",sa.Boolean,nullable=False),
    col("obtained_at",sa.DateTime(timezone=True),nullable=False),col("updated_at",sa.DateTime(timezone=True),nullable=False),
    sa.Column("run_id",sa.String(36),sa.ForeignKey("ingestion_runs.id"),nullable=False),
    col("provenance",sa.JSON,nullable=False),
    sa.PrimaryKeyConstraint(*DIM,"timestamp"), feed_fk(),
    sa.CheckConstraint("volume >= 0"),sa.CheckConstraint('"low" > 0 AND "high" >= "low" AND "open" BETWEEN "low" AND "high" AND "close" BETWEEN "low" AND "high"'))
sa.Index("ix_market_instrument_timestamp",bars.c.instrument_id,bars.c.timestamp)
sa.Index("ix_market_timestamp",bars.c.timestamp)
states = table("ingestion_state",*dims(),col("cursor",sa.DateTime(timezone=True),nullable=False),sa.PrimaryKeyConstraint(*DIM),feed_fk())
coverage = table("ingestion_coverage",*dims(),col("start",sa.DateTime(timezone=True),nullable=False),col("end",sa.DateTime(timezone=True),nullable=False),sa.PrimaryKeyConstraint(*DIM,"start","end"),feed_fk())
actions = table("corporate_actions",sa.Column("instrument_id",sa.String(36),sa.ForeignKey("instruments.id"),nullable=False),sa.Column("provider",sa.String(32),sa.ForeignKey("data_providers.name"),nullable=False),
    col("kind",sa.String(32),nullable=False),col("ex_date",sa.Date,nullable=False),col("external_id",sa.String(128),nullable=False),col("value",sa.Numeric(24,10),nullable=False),col("metadata",sa.JSON,nullable=False),
    sa.Column("run_id",sa.String(36),sa.ForeignKey("ingestion_runs.id"),nullable=False),col("updated_at",sa.DateTime(timezone=True),nullable=False),
    sa.PrimaryKeyConstraint("instrument_id","provider","kind","ex_date","external_id"))
issues = table("data_quality_issues",col("id",sa.String(36),primary_key=True),*dims(),sa.Column("run_id",sa.String(36),sa.ForeignKey("ingestion_runs.id"),nullable=False),col("code",sa.String(32),nullable=False),col("message",sa.Text,nullable=False),col("timestamp",sa.DateTime(timezone=True)),col("created_at",sa.DateTime(timezone=True),nullable=False),feed_fk())
# Revision log keeps old/new values for corrections (bars and corporate actions).
revisions = table("data_revisions",col("id",sa.String(36),primary_key=True),col("entity",sa.String(32),nullable=False),col("identity",sa.JSON,nullable=False),col("previous",sa.JSON,nullable=False),col("current",sa.JSON,nullable=False),sa.Column("run_id",sa.String(36),sa.ForeignKey("ingestion_runs.id"),nullable=False),col("created_at",sa.DateTime(timezone=True),nullable=False))
