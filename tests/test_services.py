from datetime import datetime, UTC, timedelta, date
from dataclasses import replace
from decimal import Decimal as D
from types import SimpleNamespace
import pytest
from stock_predictor.domain.models import *
from stock_predictor.services.calendar import Calendar
from stock_predictor.services.ingestion import IngestionService
from stock_predictor.services.validation import validate_bar, read_tickers
from stock_predictor.services.queries import QueryService, aggregate
from stock_predictor.settings import Settings

class FakeProvider:
    name = "yahoo"
    def get_capabilities(self): return Capabilities(("1d",),("default",),("split",),{"1d":None},{"1d":1},False,True,"test")
    def get_instrument_info(self,ticker): return {}
    def get_historical_bars(self,instrument,series,start,end):
        calendar = Calendar()
        return [MarketBar(series,t,t.astimezone(__import__('zoneinfo').ZoneInfo("America/New_York")).date(),D(10),D(12),D(9),D(11),1,True) for t in calendar.expected(start,end,"1d")]

def test_calendar_dst_early_close():
    c = Calendar()
    assert c.bounds(date(2025,11,28))[1].hour==18
    assert c.bounds(date(2025,7,4)) is None
    assert c.bounds(date(2025,3,7))[0].hour==14
    assert c.bounds(date(2025,3,10))[0].hour==13
    assert len(list(c.expected(datetime(2025,11,28,tzinfo=UTC),datetime(2025,11,29,tzinfo=UTC),"1m")))==210

def test_csv(tmp_path):
    f = tmp_path/"tickers.csv"
    f.write_text("ticker\n aapl \nAAPL\nBRK.B\n")
    assert read_tickers(f)==["AAPL","BRK.B"]
    f.write_text("ticker\n../bad\n")
    with pytest.raises(ValueError): read_tickers(f)

def test_validation():
    series = Series("id","yahoo","default","1d","split")
    b = MarketBar(series,datetime(2025,1,2,5,tzinfo=UTC),date(2025,1,2),D(10),D(12),D(9),D(11),0,True)
    c = Calendar()
    assert not validate_bar(b,c)
    assert validate_bar(replace(b,open=D(13)),c)[0].code=="ohlc"
    assert validate_bar(replace(b,volume=-1),c)[0].code=="volume"
    assert validate_bar(replace(b,close=D("NaN")),c)[0].code=="price"

def test_ingest_idempotent_and_export(factory,tmp_path):
    provider = FakeProvider()
    service = IngestionService(provider,factory,Settings())
    instrument = service.register("AAPL")
    series = Series(instrument.id,"yahoo","default","1d","split")
    start,end = datetime(2025,1,2,5,tzinfo=UTC),datetime(2025,1,4,5,tzinfo=UTC)
    assert service.ingest(instrument,series,start,end)["records"]==2
    assert service.ingest(instrument,series,start,end)["skipped"]==2
    query = QueryService(factory)
    assert len(query.dataframe(series,start,end))==2
    for fmt in ("csv","parquet"):
        assert query.export(series,start,end,tmp_path/f"test.{fmt}",fmt,1)==2
    import pyarrow.parquet as pq
    table = pq.read_table(tmp_path/"test.parquet")
    assert table.num_rows==2 and table["provider"].to_pylist()==["yahoo"]*2

def test_empty_does_not_advance_cursor(factory):
    p = FakeProvider()
    p.get_historical_bars = lambda *args: []
    service = IngestionService(p,factory,Settings())
    instrument = service.register("AAPL")
    series = Series(instrument.id,"yahoo","default","1d","split")
    start,end = datetime(2025,1,2,5,tzinfo=UTC),datetime(2025,1,3,5,tzinfo=UTC)
    result = service.ingest(instrument,series,start,end)
    assert result["failed"]==1
    with factory() as u:
        assert u.repositories.cursor(series) is None
        assert not u.repositories.covered(series,start,end)

def test_retries_only_transient():
    attempts = []
    service = IngestionService(FakeProvider(),None,Settings(max_retries=2),sleep=lambda x:attempts.append(x))
    def transient(): raise TransientError("rate")
    with pytest.raises(TransientError): service.retry(transient)
    assert attempts==[2,4]
    attempts.clear()
    def permanent(): raise ProviderError("auth")
    with pytest.raises(ProviderError): service.retry(permanent)
    assert attempts==[]

def test_partial_provisional(factory):
    p = FakeProvider()
    original = p.get_historical_bars
    p.get_historical_bars = lambda *args:[replace(b,complete=False) for b in original(*args)]
    service = IngestionService(p,factory,Settings())
    instrument = service.register("AAPL")
    series = Series(instrument.id,"yahoo","default","1d","split")
    start,end = datetime(2025,1,2,5,tzinfo=UTC),datetime(2025,1,3,5,tzinfo=UTC)
    assert service.ingest(instrument,series,start,end)["failed"]==1
    with factory() as u: assert u.repositories.cursor(series) is None

def test_aggregation_missing_no_interpolation():
    c = Calendar()
    series = Series("id","yahoo","default","1m","split")
    start = datetime(2025,1,2,14,30,tzinfo=UTC)
    bars = [MarketBar(series,start+timedelta(minutes=i),date(2025,1,2),D(10),D(12),D(9),D(11),1,True) for i in range(10)]
    result = list(aggregate(bars,"5m",c))
    assert len(result)==2 and result[0].volume==5 and result[0].provenance["derived"]
    assert len(list(aggregate(bars[1:],"5m",c)))==1

def test_failure_resume_and_never_skip_holes(factory):
    provider = FakeProvider()
    original = provider.get_historical_bars
    def fetch(instrument,series,start,end):
        if start.date()==date(2025,1,2): raise TransientError("temporary")
        return original(instrument,series,start,end)
    provider.get_historical_bars = fetch
    service = IngestionService(provider,factory,Settings(max_retries=0))
    instrument = service.register("AAPL")
    series = Series(instrument.id,"yahoo","default","1d","split")
    start,end = datetime(2025,1,2,5,tzinfo=UTC),datetime(2025,1,4,5,tzinfo=UTC)
    result = service.ingest(instrument,series,start,end)
    assert result["failed"]==1 and result["records"]==1
    with factory() as u:
        assert u.repositories.cursor(series) is None
        assert len(u.repositories.recent_runs())==2
    provider.get_historical_bars = original
    result = service.ingest(instrument,series,start,end)
    assert result["failed"]==0 and result["skipped"]==1
    assert len(QueryService(factory).dataframe(series,start,end))==2

def test_invalid_bars_and_duplicates_not_confirmed(factory):
    provider = FakeProvider()
    original = provider.get_historical_bars
    def fetch(*args):
        values = original(*args)
        return [replace(values[0],open=D(100)),values[0],values[0]]
    provider.get_historical_bars = fetch
    service = IngestionService(provider,factory,Settings())
    instrument = service.register("AAPL")
    series = Series(instrument.id,"yahoo","default","1d","split")
    start,end = datetime(2025,1,2,5,tzinfo=UTC),datetime(2025,1,3,5,tzinfo=UTC)
    result = service.ingest(instrument,series,start,end)
    assert result["failed"]==1
    with factory() as u:
        assert u.repositories.cursor(series) is None
        assert u.repositories.recent_issues()

def test_compare_streaming_policy(factory):
    provider = FakeProvider()
    service = IngestionService(provider,factory,Settings())
    instrument = service.register("AAPL")
    left = Series(instrument.id,"yahoo","default","1d","split")
    start,end = datetime(2025,1,2,5,tzinfo=UTC),datetime(2025,1,3,5,tzinfo=UTC)
    service.ingest(instrument,left,start,end)
    right = replace(left,provider="alpaca",feed="iex")
    with factory() as u:
        run = u.repositories.begin_run(right,start,end)
        values = [replace(b,series=right,close=D(12)) for b in u.repositories.get_bars(left,start,end)]
        u.repositories.save_bars(values,run)
        u.commit()
    query = QueryService(factory)
    differences = list(query.compare(left,right,start,end))
    assert differences[0]["close_difference"]==D(-1)
    with pytest.raises(ValueError): list(query.compare(left,replace(right,adjustment="raw"),start,end))

def test_partial_confirms_only_closed_prefix(factory):
    p = FakeProvider()
    caps = p.get_capabilities()
    p.get_capabilities = lambda:replace(caps,chunk_days={"1d":10})
    original = p.get_historical_bars
    def fetch(*args):
        values = original(*args)
        return [values[0],replace(values[1],complete=False)]
    p.get_historical_bars = fetch
    service = IngestionService(p,factory,Settings())
    instrument = service.register("AAPL")
    series = Series(instrument.id,"yahoo","default","1d","split")
    start = datetime(2025,1,2,5,tzinfo=UTC)
    boundary = datetime(2025,1,3,5,tzinfo=UTC)
    end = datetime(2025,1,4,5,tzinfo=UTC)
    assert service.ingest(instrument,series,start,end)["failed"]==1
    with factory() as u:
        assert u.repositories.cursor(series)==boundary
        assert u.repositories.covered(series,start,boundary)
        assert not u.repositories.covered(series,start,end)
