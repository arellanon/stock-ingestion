"""Provider SDK objects never escape this module."""
from datetime import datetime, timedelta, UTC
from decimal import Decimal
from dataclasses import replace
from math import ceil
import threading
from zoneinfo import ZoneInfo
from stock_predictor.domain.models import (Capabilities, CorporateAction, MarketBar,
    ProviderError, TransientError, UnsupportedOperation)
from stock_predictor.services.calendar import Calendar
NY = ZoneInfo("America/New_York")

def translate_error(exc):
    status = getattr(exc, "status_code", None)
    response = getattr(exc, "response", None)
    if response is not None: status = getattr(response, "status_code", status)
    name = type(exc).__name__.lower()
    if status in {401,403}: return ProviderError("Provider denied authentication/entitlement")
    if status == 429 or (status and status >= 500) or any(x in name for x in ("timeout", "connection", "ratelimit")):
        return TransientError("Temporary provider network/rate-limit error")
    return ProviderError(f"Provider request failed ({type(exc).__name__}); check symbol, coverage and credentials")

class BaseProvider:
    def __init__(self): self.calendar = Calendar()
    def get_supported_timeframes(self): return self.get_capabilities().intervals
    def get_provider_name(self): return self.name
    def check(self, series):
        cap = self.get_capabilities()
        if series.interval == "1d" and series.session == "extended":
            raise UnsupportedOperation("Daily extended candles have no consistent provider definition")
        if (series.interval not in cap.intervals or series.feed not in cap.feeds
                or series.adjustment not in cap.adjustments or series.provider != self.name):
            raise UnsupportedOperation("Unsupported interval, feed, adjustment or provider")
    def bar(self, series, stamp, o, h, l, c, volume, provenance=None):
        stamp = stamp.astimezone(UTC)
        # Daily bar anchors are normalized to NY local midnight in BOTH adapters.
        if series.interval == "1d":
            stamp = datetime.combine(stamp.astimezone(NY).date(), datetime.min.time(), NY).astimezone(UTC)
        day, session, complete = self.calendar.classify(stamp, series.interval)
        if session == "closed" or (series.session == "regular" and session != "regular"): return None
        if series.session == "extended" and session != "extended": return None
        return MarketBar(series, stamp, day, *(Decimal(str(v)) for v in (o,h,l,c)),
                         int(volume), complete, provenance=provenance or {})

class YahooProvider(BaseProvider):
    name = "yahoo"
    def __init__(self, ticker_factory=None):
        super().__init__()
        if ticker_factory is None:
            import yfinance as yf
            ticker_factory = yf.Ticker
        self.ticker_factory = ticker_factory
    def get_capabilities(self):
        return Capabilities(("1d","1m","5m","15m","60m"), ("default",),
            ("split", "split_dividend"), {"1d":None,"1m":7,"5m":60,"15m":60,"60m":730},
            {"1d":365,"1m":6,"5m":30,"15m":30,"60m":90}, True, True,
            "Best effort limits; availability can vary. auto_adjust=False still reflects Yahoo split history; raw historical trade prices are not guaranteed.")
    def get_historical_bars(self, instrument, series, start, end):
        self.check(series)
        try:
            frame = self.ticker_factory(instrument.ticker.replace(".", "-")).history(start=start, end=end,
                interval=series.interval, auto_adjust=series.adjustment == "split_dividend",
                back_adjust=False, actions=False, prepost=series.session == "extended",
                raise_errors=True, timeout=30)
        except Exception as exc: raise translate_error(exc) from None
        result = []
        for stamp, row in frame.iterrows():
            if stamp.tzinfo is None: raise ProviderError("Yahoo returned timezone-free timestamp")
            bar = self.bar(series, stamp.to_pydatetime(), row.Open, row.High, row.Low,
                           row.Close, row.Volume)
            if bar and start <= bar.timestamp < end: result.append(bar)
        return result
    def get_corporate_actions(self, instrument, start, end):
        try:
            frame = self.ticker_factory(instrument.ticker.replace(".", "-")).history(start=start, end=end,
                interval="1d", auto_adjust=False, actions=True, raise_errors=True, timeout=30)
        except Exception as exc: raise translate_error(exc) from None
        result = []
        for stamp, row in frame.iterrows():
            for column, kind in (("Dividends","dividend"),("Stock Splits","split")):
                value = row.get(column, 0)
                if value:
                    result.append(CorporateAction(instrument.id, self.name, kind,
                        stamp.date(), Decimal(str(value)), metadata={"source_column":column}))
        return result
    def get_instrument_info(self, ticker):
        try:
            info = self.ticker_factory(ticker.replace(".", "-")).get_info()
            return {k: info[k] for k in ("shortName","exchange","currency","sector","quoteType") if k in info}
        except Exception as exc: raise translate_error(exc) from None

class AlpacaProvider(BaseProvider):
    name = "alpaca"
    def __init__(self, key, secret, feed="iex", bars_client=None, actions_client=None):
        super().__init__()
        if not key or not secret: raise ProviderError("ALPACA_API_KEY and ALPACA_SECRET_KEY are required")
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.historical.corporate_actions import CorporateActionsClient
        self.client = bars_client or StockHistoricalDataClient(key, secret)
        self.actions_client = actions_client or CorporateActionsClient(key, secret)
        # Bound SDK HTTP timeout/retries; service owns backoff.
        for client in (self.client, self.actions_client):
            client._retry = 0
            if hasattr(client, "_session"):
                from functools import partial
                client._session.request = partial(client._session.request, timeout=30)
        self.feed = feed
    def get_capabilities(self):
        return Capabilities(("1d","1m","5m","15m","60m"), ("iex","sip"),
            ("raw","split","dividend","split_dividend"),
            {i:None for i in ("1d","1m","5m","15m","60m")},
            {"1d":365,"1m":1,"5m":7,"15m":14,"60m":30}, True, False,
            "Historical coverage unknown; feed and corporate-action access depend on account entitlements. IEX is not consolidated SIP.")
    def get_historical_bars(self, instrument, series, start, end):
        self.check(series)
        # Native hourly bars are clock-aligned and may straddle the open.
        # Derive session-anchored 60m bars from minutes to avoid mixing sessions.
        if series.interval == "60m":
            from stock_predictor.services.queries import aggregate
            if series.session != "regular":
                raise UnsupportedOperation("60m extended aggregation is not implemented")
            minute_series = replace(series, interval="1m")
            minutes = self.get_historical_bars(instrument, minute_series, start, end)
            return list(aggregate(minutes, "60m", self.calendar))
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
        from alpaca.data.enums import Adjustment, DataFeed
        tf = TimeFrame.Day if series.interval == "1d" else TimeFrame(int(series.interval[:-1]), TimeFrameUnit.Minute)
        adjustment = "all" if series.adjustment == "split_dividend" else series.adjustment
        try:
            # SDK _get_marketdata follows ALL next_page_token values (no total limit).
            response = self.client.get_stock_bars(StockBarsRequest(symbol_or_symbols=instrument.ticker,
                start=start, end=end - timedelta(microseconds=1), timeframe=tf,
                adjustment=Adjustment(adjustment), feed=DataFeed(series.feed)))
        except Exception as exc: raise translate_error(exc) from None
        result = []
        for value in response.data.get(instrument.ticker, []):
            bar = self.bar(series, value.timestamp, value.open,value.high,value.low,value.close,value.volume)
            if bar and start <= bar.timestamp < end: result.append(bar)
        return result
    def get_corporate_actions(self, instrument, start, end):
        from alpaca.data.requests import CorporateActionsRequest
        try:
            response = self.actions_client.get_corporate_actions(CorporateActionsRequest(
                symbols=[instrument.ticker], start=start.date(), end=end.date()))
        except Exception as exc: raise translate_error(exc) from None
        result = []
        for group, events in response.data.items():
            for event in events:
                if not event.ex_date or not start.date() <= event.ex_date < end.date(): continue
                if group == "cash_dividends": kind, value = "dividend", Decimal(str(event.rate))
                elif group in {"forward_splits","reverse_splits"}:
                    kind, value = "split", Decimal(str(event.new_rate)) / Decimal(str(event.old_rate))
                else: continue
                result.append(CorporateAction(instrument.id,self.name,kind,event.ex_date,value,
                    str(event.id),event.model_dump(mode="json")))
        return result
    def get_instrument_info(self, ticker):
        raise UnsupportedOperation("Asset metadata requires separate trading API authorization; not requested by this adapter")

class _IBKRHistoryClient:
    """One-shot ibapi client used by IBKRProvider; SDK objects stay private."""
    terminal_errors = {162, 165, 200, 321, 502, 504, 10167, 10168}

    def __init__(self, contract, request, host, port, client_id, timeout):
        from ibapi.client import EClient
        from ibapi.wrapper import EWrapper

        owner = self

        class Callback(EWrapper):
            def nextValidId(self, order_id):
                owner.next_valid_id(order_id)

            def historicalData(self, req_id, bar):
                owner.historical_data(req_id, bar)

            def historicalDataEnd(self, req_id, start, end):
                owner.historical_data_end(req_id, start, end)

            def error(self, req_id, error_code, error_string, advanced_order_reject_json=""):
                owner.error(req_id, error_code, error_string, advanced_order_reject_json)

        self.contract = contract
        self.request = request
        self.host = host
        self.port = port
        self.client_id = client_id
        self.timeout = timeout
        self.finished = threading.Event()
        self.bars = []
        self.failure = None
        self.app = EClient(Callback())

    def next_valid_id(self, _order_id):
        self.app.reqHistoricalData(1, self.contract, *self.request)

    def historical_data(self, req_id, bar):
        if req_id == 1:
            self.bars.append(bar)

    def historical_data_end(self, req_id, _start, _end):
        if req_id == 1:
            self.finished.set()

    def error(self, req_id, error_code, error_string, _advanced_order_reject_json=""):
        if error_code in self.terminal_errors:
            self.failure = (req_id, error_code, error_string)
            self.finished.set()

    def run(self):
        self.app.connect(self.host, self.port, clientId=self.client_id)
        thread = threading.Thread(target=self.app.run, daemon=True)
        thread.start()
        if not self.finished.wait(timeout=self.timeout):
            raise TransientError("Interactive Brokers historical data request timed out")
        if self.failure:
            _, code, message = self.failure
            if code in {502, 504}:
                raise TransientError(f"Interactive Brokers connection failed ({code})")
            raise ProviderError(f"Interactive Brokers request failed ({code}): {message}")
        return self.bars

    def close(self):
        if self.app.isConnected():
            self.app.disconnect()


class IBKRProvider(BaseProvider):
    name = "ibkr"

    def __init__(self, host="127.0.0.1", port=7497, client_id=10, timeout=45,
                 exchange="SMART", currency="USD", client_factory=None):
        super().__init__()
        self.host = host
        self.port = port
        self.client_id = client_id
        self.timeout = timeout
        self.exchange = exchange
        self.currency = currency
        self.client_factory = client_factory or _IBKRHistoryClient

    def get_capabilities(self):
        return Capabilities(
            ("1d", "1m", "5m", "15m", "60m"), ("smart",), ("raw",),
            {interval: None for interval in ("1d", "1m", "5m", "15m", "60m")},
            {"1d": 365, "1m": 1, "5m": 7, "15m": 30, "60m": 30},
            False, False,
            "Requires TWS or IB Gateway, market-data permissions and a qualified IBKR contract.")

    @staticmethod
    def _bar_size(interval):
        return {"1d": "1 day", "1m": "1 min", "5m": "5 mins",
                "15m": "15 mins", "60m": "1 hour"}[interval]

    @staticmethod
    def _duration(start, end):
        days = max(1, ceil((end - start).total_seconds() / 86400))
        if days <= 1: return "1 D"
        if days <= 7: return "7 D"
        if days <= 30: return "30 D"
        return "1 Y"

    @staticmethod
    def _timestamp(value, interval):
        text = str(value)
        if text.isdigit() and len(text) >= 10:
            stamp = datetime.fromtimestamp(int(text), UTC)
        elif interval == "1d" and len(text) == 8:
            stamp = datetime.strptime(text, "%Y%m%d").replace(tzinfo=NY).astimezone(UTC)
        else:
            date_part, time_part = text[:8], text[9:17]
            stamp = datetime.strptime(f"{date_part} {time_part}", "%Y%m%d %H:%M:%S").replace(tzinfo=NY).astimezone(UTC)
        return stamp

    def get_historical_bars(self, instrument, series, start, end):
        self.check(series)
        from ibapi.contract import Contract

        contract = Contract()
        contract.symbol = instrument.ticker
        contract.secType = "STK"
        contract.exchange = self.exchange
        contract.currency = self.currency
        request = (
            end.astimezone(UTC).strftime("%Y%m%d %H:%M:%S UTC"),
            self._duration(start, end),
            self._bar_size(series.interval),
            "TRADES",
            series.session == "regular",
            2,
            False,
            []
        )
        client = self.client_factory(contract, request, self.host, self.port,
                                     self.client_id, self.timeout)
        try:
            raw_bars = client.run()
        except ProviderError:
            raise
        except Exception as exc:
            raise translate_error(exc) from None
        finally:
            client.close()
        result = []
        for raw in raw_bars:
            stamp = self._timestamp(raw.date, series.interval)
            bar = self.bar(series, stamp, raw.open, raw.high, raw.low,
                           raw.close, raw.volume,
                           {"source": "ibkr", "what_to_show": "TRADES"})
            if bar and start <= bar.timestamp < end:
                result.append(bar)
        return result

    def get_corporate_actions(self, instrument, start, end):
        raise UnsupportedOperation("IBKR corporate actions are not implemented by this adapter")

    def get_instrument_info(self, ticker):
        return {}


REGISTRY = {"yahoo": lambda settings: YahooProvider(),
            "alpaca": lambda settings: AlpacaProvider(settings.alpaca_api_key,
                              settings.alpaca_secret_key, settings.alpaca_data_feed),
            "ibkr": lambda settings: IBKRProvider(settings.ibkr_host, settings.ibkr_port,
                              settings.ibkr_client_id, settings.ibkr_timeout_seconds,
                              settings.ibkr_exchange, settings.ibkr_currency)}

def create_provider(name, settings):
    try: factory = REGISTRY[name]
    except KeyError: raise UnsupportedOperation(f"Unknown provider: {name}") from None
    return factory(settings)
