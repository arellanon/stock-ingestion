from datetime import datetime, timedelta, UTC
from pathlib import Path
from typing import Optional
import json
import logging
import typer
from stock_predictor.settings import Settings
from stock_predictor.adapters.providers import create_provider
from stock_predictor.adapters.persistence import create_uow_factory
from stock_predictor.domain.models import Series
from stock_predictor.services.ingestion import IngestionService
from stock_predictor.services.queries import QueryService
from stock_predictor.services.validation import read_tickers, validate_bar
from stock_predictor.services.calendar import Calendar
app = typer.Typer(no_args_is_help=True,help="Historical market data ingestion; no trading or prediction.")

def timestamp(value,default):
    if value is None: return default
    dt = datetime.fromisoformat(value.replace("Z","+00:00"))
    # Date-only arguments define UTC bounds. Explicit NY offsets recommended for intraday.
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)

def setup():
    settings = Settings()
    logging.basicConfig(level=settings.log_level,format="%(asctime)s %(levelname)s %(message)s")
    return settings,create_uow_factory(settings)

def run_load(update,ticker,provider,feed,interval,start,end,force,adjustment,session):
    settings,factory = setup()
    source = create_provider(provider or settings.market_data_provider,settings)
    cap = source.get_capabilities()
    feed = feed or (settings.alpaca_data_feed if source.name=="alpaca" else ("smart" if source.name=="ibkr" else "default"))
    adjustment = adjustment or ("split" if source.name=="yahoo" else "raw")
    sessions = [session] if session else (["regular"] if settings.regular_session_only else ["regular", "extended"])
    intervals = [interval] if interval else (["1d"] if settings.download_daily else []) + (settings.intraday_intervals.split(",") if settings.download_intraday else [])
    now = datetime.now(UTC)
    start,end = timestamp(start,now-timedelta(days=366*settings.default_history_years)),timestamp(end,now)
    tickers = [ticker.upper()] if ticker else read_tickers(settings.tickers_file)
    service = IngestionService(source,factory,settings)
    failed = 0
    for symbol in tickers:
        instrument = service.register(symbol)
        if not instrument.enabled: continue
        for tf in intervals:
            for selected_session in sessions:
                if tf == "1d" and selected_session == "extended" and session is None: continue
                series = Series(instrument.id,source.name,feed,tf,adjustment,selected_session)
                result = service.ingest(instrument,series,start,end,incremental=update,force=force)
                typer.echo(json.dumps({"ticker":symbol,"interval":tf,"session":selected_session,"provider":source.name,**result}))
                failed += result["failed"]
    if failed: raise typer.Exit(2)

@app.command()
def backfill(ticker:Optional[str]=None,provider:Optional[str]=None,feed:Optional[str]=None,
             interval:Optional[str]=None,start:Optional[str]=None,end:Optional[str]=None,
             force:bool=False,adjustment:Optional[str]=None,session:Optional[str]=None):
    """Download history in resumable blocks; --force reconciles existing ranges."""
    run_load(False,ticker,provider,feed,interval,start,end,force,adjustment,session)

@app.command()
def update(ticker:Optional[str]=None,provider:Optional[str]=None,feed:Optional[str]=None,
           interval:Optional[str]=None,start:Optional[str]=None,end:Optional[str]=None,
           adjustment:Optional[str]=None,session:Optional[str]=None):
    """Refresh overlap plus new history; partial ranges never advance cursor."""
    run_load(True,ticker,provider,feed,interval,start,end,False,adjustment,session)

@app.command()
def status():
    """Coverage per complete series identity."""
    settings,factory = setup()
    with factory() as uow:
        typer.echo(json.dumps({"instruments":[{"id":x.id,"ticker":x.ticker,"enabled":x.enabled} for x in uow.repositories.instruments()],"coverage":uow.repositories.coverage(),"recent_runs":uow.repositories.recent_runs(),"recent_issues":uow.repositories.recent_issues()},indent=2))

def selection(factory,ticker,provider,feed,interval,adjustment,session):
    with factory() as uow:
        instrument = uow.repositories.resolve(ticker.upper(),provider)
    if instrument is None: raise typer.BadParameter("Instrument not found for this provider")
    return Series(instrument.id,provider,feed,interval,adjustment,session)

@app.command(name="inspect")
def inspect_series(ticker:str,interval:str="1d",provider:str="yahoo",feed:str="default",
                   adjustment:str="split",session:str="regular",start:Optional[str]=None,end:Optional[str]=None):
    """Show recent bars without merging sources."""
    settings,factory = setup()
    series = selection(factory,ticker,provider,feed,interval,adjustment,session)
    query = QueryService(factory)
    frame = query.dataframe(series,timestamp(start,datetime.now(UTC)-timedelta(days=10)),timestamp(end,datetime.now(UTC)))
    typer.echo(frame.tail(20).to_string(index=False))

@app.command()
def export(ticker:str,format:str="parquet",output:str="export.parquet",interval:str="1d",
           provider:str="yahoo",feed:str="default",adjustment:str="split",session:str="regular",
           start:Optional[str]=None,end:Optional[str]=None):
    """Stream CSV/Parquet in bounded batches."""
    settings,factory = setup()
    series = selection(factory,ticker,provider,feed,interval,adjustment,session)
    count = QueryService(factory).export(series,timestamp(start,datetime(1990,1,1,tzinfo=UTC)),timestamp(end,datetime.now(UTC)),output,format,settings.batch_size)
    typer.echo(f"Exported {count} rows to {output}")

@app.command()
def validate(ticker:str,interval:str="1d",provider:str="yahoo",feed:str="default",
             adjustment:str="split",session:str="regular",start:Optional[str]=None,end:Optional[str]=None):
    """Read-only OHLCV/calendar validation and potential gap count."""
    settings,factory = setup()
    series = selection(factory,ticker,provider,feed,interval,adjustment,session)
    left,right = timestamp(start,datetime.now(UTC)-timedelta(days=30)),timestamp(end,datetime.now(UTC))
    calendar = Calendar()
    observed,errors = set(),0
    for bar in QueryService(factory).bars(series,left,right):
        observed.add(bar.timestamp)
        errors+=len(validate_bar(bar,calendar))
    missing = sum(stamp not in observed and calendar.classify(stamp,interval)[2] for stamp in calendar.expected(left,right,interval)) if session=="regular" else None
    typer.echo(json.dumps({"invalid":errors,"potential_missing":missing,"note":"Absent timestamps may represent no trades; no interpolation"}))
    if errors: raise typer.Exit(2)

@app.command()
def enable(ticker: str, enabled: bool = True):
    """Enable/disable updates; --no-enabled conserves all historical data."""
    settings,factory = setup()
    with factory() as uow:
        candidates = [i for i in uow.repositories.instruments() if i.ticker==ticker.upper()]
        if len(candidates)!=1: raise typer.BadParameter("Instrument missing or ambiguous")
        uow.repositories.set_enabled(candidates[0].id, enabled)
        uow.commit()
    typer.echo(f"{ticker}: enabled={enabled}")

@app.command(name="map-identifier")
def map_identifier(instrument_id: str, provider: str, symbol: str, external_id: str = ""):
    """Explicitly bind a verified provider symbol to an existing internal instrument."""
    settings,factory = setup()
    with factory() as uow:
        uow.repositories.bind_identifier(instrument_id,provider,symbol.upper(),external_id)
        uow.commit()
    typer.echo("Identifier bound")
