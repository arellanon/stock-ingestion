from dataclasses import asdict
from datetime import datetime, UTC, date
from uuid import uuid4
import json
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from stock_predictor.adapters import schema as s
from stock_predictor.domain.models import Instrument, Series, MarketBar, CorporateAction

def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

def dimensions(series): return asdict(series)
def where_series(table, series):
    return sa.and_(*(table.c[k] == value for k,value in dimensions(series).items()))
def serial(row): return json.loads(json.dumps(dict(row),default=str))

class SQLRepositories:
    def __init__(self, connection, batch_size=1000):
        self.connection = connection
        self.batch_size = batch_size
        dialect = connection.dialect.name
        if dialect not in {"postgresql","sqlite"}: raise ValueError("Install a repository adapter for this backend")
        self.insert = pg_insert if dialect == "postgresql" else sqlite_insert
    def upsert(self, table, rows, keys, update=True):
        limit = min(self.batch_size, max(1, (60000 if self.connection.dialect.name == "postgresql" else 900) // len(table.columns)))
        for offset in range(0,len(rows),limit):
            statement = self.insert(table).values(rows[offset:offset+limit])
            if update:
                statement = statement.on_conflict_do_update(index_elements=keys,
                    set_={c.name:getattr(statement.excluded,c.name) for c in table.columns if c.name not in keys})
            else: statement = statement.on_conflict_do_nothing(index_elements=keys)
            self.connection.execute(statement)
    def source(self, provider, feed="default"):
        self.upsert(s.providers,[{"name":provider}],["name"],False)
        self.upsert(s.feeds,[{"provider":provider,"name":feed}],["provider","name"],False)
    def register(self, ticker, provider, metadata):
        self.source(provider)
        existing = self.connection.execute(sa.select(s.instruments).join(s.identifiers,s.identifiers.c.instrument_id==s.instruments.c.id).where(
            s.identifiers.c.provider==provider,s.identifiers.c.symbol==ticker,s.identifiers.c.valid_to.is_(None))).mappings().one_or_none()
        if existing:
            if metadata: self.connection.execute(s.instruments.update().where(s.instruments.c.id==existing["id"]).values(metadata=metadata))
            return Instrument(existing["id"],existing["ticker"],existing["enabled"],metadata or existing["metadata"])
        # No automatic equivalence inferred from a ticker across providers.
        # Call bind_identifier explicitly after verifying the instrument identity.
        instrument_id = str(uuid4())
        self.connection.execute(s.instruments.insert().values(id=instrument_id,ticker=ticker,enabled=True,metadata=metadata))
        self.connection.execute(s.identifiers.insert().values(id=str(uuid4()),instrument_id=instrument_id,
            provider=provider,symbol=ticker,external_id=str(metadata.get("id","")),valid_from=date.today(),valid_to=None))
        return Instrument(instrument_id,ticker,True,metadata)
    def resolve(self, symbol, provider):
        row = self.connection.execute(sa.select(s.instruments).join(s.identifiers,s.identifiers.c.instrument_id==s.instruments.c.id).where(s.identifiers.c.provider==provider,s.identifiers.c.symbol==symbol,s.identifiers.c.valid_to.is_(None))).mappings().one_or_none()
        return Instrument(**dict(row)) if row else None
    def bind_identifier(self, instrument_id, provider, symbol, external_id=""):
        self.source(provider)
        if self.resolve(symbol,provider) is not None: raise ValueError("Active identifier already bound; retire it explicitly first")
        self.connection.execute(s.identifiers.insert().values(id=str(uuid4()),instrument_id=instrument_id,provider=provider,symbol=symbol,external_id=external_id,valid_from=date.today(),valid_to=None))
    def retire_identifier(self, provider, symbol, valid_to):
        self.connection.execute(s.identifiers.update().where(s.identifiers.c.provider==provider,s.identifiers.c.symbol==symbol,s.identifiers.c.valid_to.is_(None)).values(valid_to=valid_to))
    def instruments(self):
        return [Instrument(**dict(row)) for row in self.connection.execute(sa.select(s.instruments)).mappings()]
    def set_enabled(self, instrument_id, enabled):
        result = self.connection.execute(s.instruments.update().where(s.instruments.c.id==instrument_id).values(enabled=enabled))
        if result.rowcount != 1: raise ValueError("Instrument not found")
    def recent_runs(self, limit=20):
        return [serial(row) for row in self.connection.execute(sa.select(s.runs).order_by(s.runs.c.started_at.desc()).limit(limit)).mappings()]
    def recent_issues(self, limit=20):
        return [serial(row) for row in self.connection.execute(sa.select(s.issues).order_by(s.issues.c.created_at.desc()).limit(limit)).mappings()]
    def get_missing_ranges(self, series, start, end):
        """Unconfirmed ingestion coverage, NOT a claim of missing traded candles."""
        rows = self.connection.execute(sa.select(s.coverage.c.start,s.coverage.c.end).where(where_series(s.coverage,series),s.coverage.c.end>start,s.coverage.c.start<end).order_by(s.coverage.c.start))
        result, cursor = [], start
        for left,right in rows:
            left,right = max(start,utc(left)),min(end,utc(right))
            if left>cursor: result.append((cursor,left))
            cursor = max(cursor,right)
        if cursor<end: result.append((cursor,end))
        return result
    def lock_series(self, series):
        if self.connection.dialect.name == "postgresql":
            import hashlib
            key = int.from_bytes(hashlib.sha256(json.dumps(dimensions(series),sort_keys=True).encode()).digest()[:8], "big", signed=True)
            self.connection.execute(sa.select(sa.func.pg_advisory_xact_lock(key)))
    def save_bars(self, bars, run_id):
        if not bars: return 0
        series = bars[0].series
        self.lock_series(series)
        if any(b.series != series for b in bars): raise ValueError("One series per batch required")
        self.source(series.provider,series.feed)
        now = datetime.now(UTC)
        for offset in range(0,len(bars),self.batch_size):
            chunk = bars[offset:offset+self.batch_size]
            previous = {utc(row["timestamp"]):row for row in self.connection.execute(sa.select(s.bars).where(
                where_series(s.bars,series),s.bars.c.timestamp.in_([b.timestamp for b in chunk]))).mappings()}
            rows, revisions = [], []
            for b in chunk:
                row = dict(**dimensions(series),timestamp=b.timestamp,session_date=b.session_date,
                    open=b.open,high=b.high,low=b.low,close=b.close,volume=b.volume,complete=b.complete,
                    obtained_at=b.obtained_at,updated_at=now,run_id=run_id,provenance=b.provenance)
                old = previous.get(b.timestamp)
                if old and any(old[k]!=row[k] for k in ("open","high","low","close","volume","complete")):
                    revisions.append(dict(id=str(uuid4()),entity="market_data",identity=serial({**dimensions(series),"timestamp":b.timestamp}),
                        previous=serial(old),current=serial(row),run_id=run_id,created_at=now))
                rows.append(row)
            if revisions: self.connection.execute(s.revisions.insert(),revisions)
            self.upsert(s.bars,rows,s.DIM+["timestamp"])
        return len(bars)
    def get_bars(self,series,start,end,batch_size=1000):
        # Keyset paging: bounded result sets, no OFFSET or full-history materialization.
        cursor = None
        while True:
            statement = sa.select(s.bars).where(where_series(s.bars,series),s.bars.c.timestamp>=start,s.bars.c.timestamp<end)
            if cursor is not None: statement = statement.where(s.bars.c.timestamp>cursor)
            rows = self.connection.execute(statement.order_by(s.bars.c.timestamp).limit(batch_size)).mappings().all()
            if not rows: break
            for r in rows:
                yield MarketBar(series,utc(r["timestamp"]),r["session_date"],r["open"],r["high"],r["low"],r["close"],r["volume"],r["complete"],utc(r["obtained_at"]),r["provenance"])
            cursor = rows[-1]["timestamp"]
    def get_latest_bar(self,series,start,end):
        row = self.connection.execute(sa.select(s.bars).where(where_series(s.bars,series),s.bars.c.timestamp>=start,s.bars.c.timestamp<end).order_by(s.bars.c.timestamp.desc()).limit(1)).mappings().one_or_none()
        if row is None: return None
        return MarketBar(series,utc(row["timestamp"]),row["session_date"],row["open"],row["high"],row["low"],row["close"],row["volume"],row["complete"],utc(row["obtained_at"]),row["provenance"])
    def coverage(self):
        result = self.connection.execute(sa.select(*(s.bars.c[k] for k in s.DIM),sa.func.min(s.bars.c.timestamp).label("start"),sa.func.max(s.bars.c.timestamp).label("end"),sa.func.count().label("count"),sa.func.max(s.bars.c.updated_at).label("updated_at")).group_by(*(s.bars.c[k] for k in s.DIM))).mappings()
        return [serial(row) for row in result]
    def save_actions(self,actions,run_id):
        # Corporate actions are small, unlike bars; preserving every revision is intentional.
        for action in actions:
            row = asdict(action)
            keys = ["instrument_id","provider","kind","ex_date","external_id"]
            old = self.connection.execute(sa.select(s.actions).where(sa.and_(*(s.actions.c[k]==row[k] for k in keys)))).mappings().one_or_none()
            row.update(run_id=run_id,updated_at=datetime.now(UTC))
            if old and (old["value"] != row["value"] or old["metadata"] != row["metadata"]):
                self.connection.execute(s.revisions.insert().values(id=str(uuid4()),entity="corporate_actions",identity=serial({k:row[k] for k in keys}),previous=serial(old),current=serial(row),run_id=run_id,created_at=datetime.now(UTC)))
            self.upsert(s.actions,[row],keys)
    def actions(self,instrument_id,provider):
        return [CorporateAction(**{k:r[k] for k in CorporateAction.__dataclass_fields__}) for r in self.connection.execute(sa.select(s.actions).where(s.actions.c.instrument_id==instrument_id,s.actions.c.provider==provider).order_by(s.actions.c.ex_date)).mappings()]
    def cursor(self,series):
        value = self.connection.execute(sa.select(s.states.c.cursor).where(where_series(s.states,series))).scalar_one_or_none()
        return utc(value) if value else None
    def set_cursor(self,series,cursor):
        self.lock_series(series)
        previous = self.cursor(series)
        if previous is None or previous<cursor: self.upsert(s.states,[{**dimensions(series),"cursor":cursor}],s.DIM)
    def covered(self,series,start,end):
        rows = self.connection.execute(sa.select(s.coverage.c.start,s.coverage.c.end).where(where_series(s.coverage,series),s.coverage.c.end>=start,s.coverage.c.start<=end).order_by(s.coverage.c.start))
        position = start
        for left,right in rows:
            left,right = utc(left),utc(right)
            if left>position: return False
            position = max(position,right)
            if position>=end: return True
        return False
    def mark_covered(self,series,start,end):
        self.upsert(s.coverage,[{**dimensions(series),"start":start,"end":end}],s.DIM+["start","end"],False)
    def begin_run(self,series,start,end):
        self.source(series.provider,series.feed)
        identifier = str(uuid4())
        self.connection.execute(s.runs.insert().values(id=identifier,**dimensions(series),start=start,end=end,started_at=datetime.now(UTC),status="running",count=0,error=""))
        return identifier
    def finish_run(self,run_id,status,count,error="",received_start=None,received_end=None):
        self.connection.execute(s.runs.update().where(s.runs.c.id==run_id).values(status=status,count=count,error=error,finished_at=datetime.now(UTC),received_start=received_start,received_end=received_end))
    def issue(self,series,run_id,issue):
        self.connection.execute(s.issues.insert().values(id=str(uuid4()),**dimensions(series),run_id=run_id,**asdict(issue),created_at=datetime.now(UTC)))

class SQLUnitOfWork:
    def __init__(self,engine,batch_size=1000): self.engine,self.batch_size = engine,batch_size
    def __enter__(self):
        self.connection = self.engine.connect()
        self.transaction = self.connection.begin()
        self.repositories = SQLRepositories(self.connection,self.batch_size)
        return self
    def commit(self): self.transaction.commit()
    def __exit__(self,*args):
        if self.transaction.is_active: self.transaction.rollback()
        self.connection.close()

def create_uow_factory(settings):
    if settings.database_backend not in {"postgresql","sqlite"}: raise ValueError("Backend adapter not installed")
    engine = sa.create_engine(settings.database_url,pool_pre_ping=True)
    if engine.dialect.name != settings.database_backend: raise ValueError("DATABASE_BACKEND/URL mismatch")
    if engine.dialect.name == "sqlite":
        @sa.event.listens_for(engine,"connect")
        def foreign_keys(dbapi_connection,record): dbapi_connection.execute("PRAGMA foreign_keys=ON")
    return lambda: SQLUnitOfWork(engine,settings.batch_size)
