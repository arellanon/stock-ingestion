from datetime import datetime, UTC, timedelta
from types import SimpleNamespace as NS
from decimal import Decimal
import pandas as pd
import pytest
from stock_predictor.adapters.providers import YahooProvider, AlpacaProvider, IBKRProvider, create_provider
from stock_predictor.domain.models import *
from stock_predictor.settings import Settings

START = datetime(2025,1,2,5,tzinfo=UTC)
END = START+timedelta(days=1)
I = Instrument("id","AAPL")

def test_yahoo_normalization_adjustment_and_actions():
    frame = pd.DataFrame({"Open":[10],"High":[12],"Low":[9],"Close":[11],"Volume":[1],"Dividends":[0.2],"Stock Splits":[0]},index=pd.DatetimeIndex(["2025-01-02"],tz="America/New_York"))
    calls = []
    def history(**kwargs): calls.append(kwargs); return frame
    p = YahooProvider(lambda ticker:NS(history=history,get_info=lambda:{"currency":"USD"}))
    s = Series("id","yahoo","default","1d","split")
    bars = p.get_historical_bars(I,s,START,END)
    assert bars[0].timestamp==START and bars[0].open==Decimal(10)
    assert calls[0]["auto_adjust"] is False and calls[0]["raise_errors"]
    assert p.get_corporate_actions(I,START,END)[0].value==Decimal("0.2")
    assert p.get_instrument_info("AAPL")=={"currency":"USD"}
    with pytest.raises(UnsupportedOperation): p.get_historical_bars(I,Series("id","yahoo","default","1d","raw"),START,END)

def test_alpaca_normalization_request():
    calls = []
    def request(r):
        calls.append(r)
        return NS(data={"AAPL":[NS(timestamp=START,open=10,high=12,low=9,close=11,volume=1)]})
    p = AlpacaProvider("fake","fake",bars_client=NS(get_stock_bars=request),actions_client=NS())
    bars = p.get_historical_bars(I,Series("id","alpaca","iex","1d","raw"),START,END)
    assert bars[0].timestamp==START and calls[0].feed.value=="iex"
    assert calls[0].limit is None
    with pytest.raises(ProviderError): AlpacaProvider("","")
    with pytest.raises(UnsupportedOperation): create_provider("missing",Settings())

def test_alpaca_sdk_pagination(monkeypatch):
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    client = StockHistoricalDataClient("fake","fake")
    tokens = []
    def get(path,data,**kwargs):
        token = data.get("page_token")
        tokens.append(token)
        return {"bars":{"AAPL":[{"t":"2025-01-02T05:00:00Z" if token is None else "2025-01-03T05:00:00Z","o":10,"h":12,"l":9,"c":11,"v":1,"n":1,"vw":10}]},"next_page_token":"page2" if token is None else None}
    monkeypatch.setattr(client,"get",get)
    result = client.get_stock_bars(StockBarsRequest(symbol_or_symbols="AAPL",timeframe=TimeFrame.Day))
    assert len(result.data["AAPL"])==2 and tokens==[None,"page2"]

def test_alpaca_corporate_action_conversion_and_error_types():
    from stock_predictor.adapters.providers import translate_error
    dividend = NS(ex_date=START.date(),rate=0.25,id="div-id",model_dump=lambda **kw:{"currency":"USD"})
    split = NS(ex_date=START.date(),new_rate=2,old_rate=1,id="split-id",model_dump=lambda **kw:{})
    client = NS(get_corporate_actions=lambda req:NS(data={"cash_dividends":[dividend],"forward_splits":[split]}))
    p = AlpacaProvider("fake","fake",bars_client=NS(),actions_client=client)
    actions = p.get_corporate_actions(I,START,END)
    assert actions[0].value==Decimal("0.25") and actions[1].value==Decimal(2)
    assert actions[0].external_id=="div-id"
    assert isinstance(translate_error(NS(status_code=429)),TransientError)
    assert type(translate_error(NS(status_code=401))) is ProviderError

def test_alpaca_hourly_session_anchor():
    stamps = pd.date_range("2025-01-02T14:30:00Z",periods=60,freq="min")
    client = NS(get_stock_bars=lambda req:NS(data={"AAPL":[NS(timestamp=t.to_pydatetime(),open=10,high=12,low=9,close=11,volume=1) for t in stamps]}))
    p = AlpacaProvider("fake","fake",bars_client=client,actions_client=NS())
    series = Series("id","alpaca","iex","60m","raw")
    bars = p.get_historical_bars(I,series,START,END)
    assert len(bars)==1 and bars[0].timestamp.hour==14 and bars[0].timestamp.minute==30
    assert bars[0].volume==60 and bars[0].provenance["derived"]

def test_ibkr_normalization_and_request():
    calls = []

    class FakeClient:
        def run(self):
            return [NS(date="20250102", open=10, high=12, low=9, close=11, volume=100)]
        def close(self):
            pass

    def factory(contract, request, host, port, client_id, timeout):
        calls.append((contract, request, host, port, client_id, timeout))
        return FakeClient()

    provider = IBKRProvider(client_factory=factory)
    series = Series("id", "ibkr", "smart", "1d", "raw")
    bars = provider.get_historical_bars(I, series, START, END)

    assert len(bars) == 1
    assert bars[0].timestamp == START
    assert bars[0].provenance["source"] == "ibkr"
    assert calls[0][0].symbol == "AAPL"
    assert calls[0][0].exchange == "SMART"
    assert calls[0][1][2] == "1 day"
    assert calls[0][1][5] == 2
