from datetime import datetime, UTC, date, timedelta
from decimal import Decimal as D
from dataclasses import replace
import sqlalchemy as sa
import pytest
from stock_predictor.domain.models import Series, MarketBar, CorporateAction
from stock_predictor.adapters import schema as s
START = datetime(2025,1,2,5,tzinfo=UTC)
END = START+timedelta(days=1)

def seed(factory):
    with factory() as u:
        instrument = u.repositories.register("AAPL","yahoo",{})
        series = Series(instrument.id,"yahoo","default","1d","split")
        run = u.repositories.begin_run(series,START,END)
        u.commit()
    return instrument,series,run

def bar(series): return MarketBar(series,START,date(2025,1,2),D(10),D(12),D(9),D(11),100,True)

def test_upsert_identity_and_revision(factory):
    instrument,series,run = seed(factory)
    with factory() as u:
        u.repositories.save_bars([bar(series)],run)
        u.repositories.save_bars([replace(bar(series),close=D(12))],run)
        alt = replace(series,adjustment="split_dividend")
        u.repositories.save_bars([bar(alt)],run)
        u.commit()
    with factory() as u:
        values = list(u.repositories.get_bars(series,START,END,1))
        assert len(values)==1 and values[0].close==D(12)
        assert len(u.repositories.coverage())==2
        assert u.connection.execute(sa.select(sa.func.count()).select_from(s.revisions)).scalar()==1

def test_rollback_bars_and_cursor(factory):
    _,series,run = seed(factory)
    with factory() as u:
        u.repositories.save_bars([bar(series)],run)
        u.repositories.set_cursor(series,END)
    with factory() as u:
        assert not list(u.repositories.get_bars(series,START,END))
        assert u.repositories.cursor(series) is None

def test_coverage_union_and_monotonic_cursor(factory):
    _,series,run = seed(factory)
    mid = START+timedelta(hours=12)
    with factory() as u:
        u.repositories.mark_covered(series,START,mid)
        assert not u.repositories.covered(series,START,END)
        u.repositories.mark_covered(series,mid,END)
        assert u.repositories.covered(series,START,END)
        u.repositories.set_cursor(series,END)
        u.repositories.set_cursor(series,START)
        assert u.repositories.cursor(series)==END
        u.commit()

def test_actions_revisions_and_source_separation(factory):
    instrument,series,run = seed(factory)
    action = CorporateAction(instrument.id,"yahoo","dividend",date(2025,1,2),D("0.2"))
    with factory() as u:
        u.repositories.save_actions([action],run)
        u.repositories.save_actions([replace(action,value=D("0.3"))],run)
        assert u.repositories.actions(instrument.id,"yahoo")[0].value==D("0.3")
        assert not u.repositories.actions(instrument.id,"alpaca")
        u.commit()

def test_foreign_keys(factory):
    with pytest.raises(sa.exc.IntegrityError):
        with factory() as u:
            u.repositories.set_cursor(Series("missing","yahoo","default","1d","split"),END)
            u.commit()

def test_disable_and_csv_removal_preserve_instrument(factory):
    instrument,series,run = seed(factory)
    with factory() as u:
        u.connection.execute(s.instruments.update().values(enabled=False))
        assert not u.repositories.register("AAPL","yahoo",{}).enabled
        u.repositories.register("MSFT","yahoo",{})
        assert len(u.repositories.instruments())==2
        u.commit()

def test_explicit_provider_identity_and_symbol_reuse(factory):
    instrument,series,run = seed(factory)
    with factory() as u:
        u.repositories.bind_identifier(instrument.id,"alpaca","AAPL","asset-id")
        assert u.repositories.resolve("AAPL","alpaca").id==instrument.id
        u.repositories.retire_identifier("yahoo","AAPL",date(2025,1,3))
        new = u.repositories.register("AAPL","yahoo",{})
        assert new.id!=instrument.id
        assert len(u.repositories.instruments())==2
        u.commit()

def test_missing_ranges_and_latest(factory):
    _,series,run = seed(factory)
    with factory() as u:
        assert u.repositories.get_missing_ranges(series,START,END)==[(START,END)]
        u.repositories.mark_covered(series,START,END)
        assert u.repositories.get_missing_ranges(series,START,END)==[]
        u.repositories.save_bars([bar(series)],run)
        assert u.repositories.get_latest_bar(series,START,END).timestamp==START
        u.commit()
