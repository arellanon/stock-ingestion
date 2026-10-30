"""Ingestion depends ONLY on domain and ports, never SDKs or SQLAlchemy."""
from datetime import datetime, timedelta, UTC
import time
from stock_predictor.domain.models import Series, ProviderError, TransientError, DataQualityIssue
from stock_predictor.services.validation import validate_bar
from stock_predictor.services.calendar import Calendar

class IngestionService:
    def __init__(self,provider,uow_factory,settings,sleep=time.sleep):
        self.provider,self.uow_factory,self.settings,self.sleep = provider,uow_factory,settings,sleep
        self.calendar = Calendar()
    def retry(self,operation):
        for attempt in range(self.settings.max_retries+1):
            try: return operation()
            except TransientError:
                if attempt==self.settings.max_retries: raise
                self.sleep(self.settings.retry_backoff_seconds*2**attempt)
    def register(self,ticker):
        capabilities = self.provider.get_capabilities()
        info = self.retry(lambda:self.provider.get_instrument_info(ticker)) if capabilities.metadata else {}
        with self.uow_factory() as uow:
            instrument = uow.repositories.register(ticker,self.provider.name,info)
            uow.commit()
        return instrument
    def ingest(self,instrument,series,start,end,incremental=False,force=False):
        if start.tzinfo is None or end.tzinfo is None or start>=end: raise ValueError("Require aware start < end")
        cap = self.provider.get_capabilities()
        if series.interval not in cap.intervals: raise ProviderError("Unsupported interval")
        with self.uow_factory() as uow:
            cursor = uow.repositories.cursor(series)
        if incremental and cursor: start = max(start,cursor-timedelta(days=self.settings.overlap_days))
        retention = cap.retention_days.get(series.interval)
        clipped = False
        if retention is not None:
            limit = datetime.now(UTC)-timedelta(days=retention)
            clipped = start<limit
            start = max(start,limit)
        if start>=end: return {"records":0,"failed":1,"clipped":clipped,"reason":"Outside provider retention"}
        summary = {"records":0,"failed":0,"skipped":0,"clipped":clipped}
        block = timedelta(days=cap.chunk_days[series.interval])
        while start<end:
            finish = min(start+block,end)
            with self.uow_factory() as uow:
                if not force and not incremental and uow.repositories.covered(series,start,finish):
                    summary["skipped"]+=1
                    start = finish
                    continue
                if not force and not incremental:
                    remaining = uow.repositories.get_missing_ranges(series,start,finish)
                    if remaining:
                        start,finish = remaining[0]
                run_id = uow.repositories.begin_run(series,start,finish)
                if clipped: uow.repositories.issue(series,run_id,DataQualityIssue("unavailable","Requested start clipped to provider best-effort retention"))
                uow.commit()
            try:
                received = self.retry(lambda:self.provider.get_historical_bars(instrument,series,start,finish))
                actions = self.retry(lambda:self.provider.get_corporate_actions(instrument,start,finish)) if cap.actions and series.interval=="1d" else []
                valid, issues, seen = [], [], set()
                previous = None
                for bar in received:
                    errors = validate_bar(bar,self.calendar)
                    if bar.series!=series or not start<=bar.timestamp<finish:
                        errors.append(DataQualityIssue("identity","Wrong series or out-of-range timestamp",bar.timestamp))
                    if bar.timestamp in seen:
                        errors.append(DataQualityIssue("duplicate","Duplicate timestamp",bar.timestamp))
                    if previous and bar.timestamp<previous:
                        issues.append(DataQualityIssue("order","Provider records not chronological",bar.timestamp))
                    previous = bar.timestamp
                    seen.add(bar.timestamp)
                    issues.extend(errors)
                    if not errors: valid.append(bar)
                valid.sort(key=lambda b:b.timestamp)
                expected = set(self.calendar.expected(start,finish,series.interval)) if series.session=="regular" else set()
                # Only evaluate ended candles. Missing bars are diagnostic, not proof of no trades.
                provisional_expected = {stamp for stamp in expected if not self.calendar.classify(stamp,series.interval)[2]}
                pending_expected = bool(provisional_expected)
                expected -= provisional_expected
                missing = expected-{bar.timestamp for bar in valid}
                if missing: issues.append(DataQualityIssue("missing",f"{len(missing)} expected timestamps absent; may indicate no trades or source gaps"))
                empty_extended = not received and series.session == "extended"
                if not received and (expected or empty_extended): issues.append(DataQualityIssue("empty","Provider returned no bars; coverage cannot be confirmed"))
                invalid = any(issue.code in {"price","ohlc","volume","timezone","identity","duplicate","session","interval","provisional"} for issue in issues)
                # Conservative coverage: no gaps, no invalid data, all returned bars definitive.
                complete = not missing and not invalid and not empty_extended and not pending_expected and finish <= datetime.now(UTC) and all(b.complete for b in valid) and bool(valid or not expected)
                boundaries = list(missing | provisional_expected) + [b.timestamp for b in valid if not b.complete]
                if finish > datetime.now(UTC): boundaries.append(datetime.now(UTC))
                confirmed_end = min([finish] + boundaries)
                prefix_confirmed = (not invalid and not empty_extended and confirmed_end > start
                                    and not any(stamp < confirmed_end for stamp in missing))
                with self.uow_factory() as uow:
                    repo = uow.repositories
                    count = repo.save_bars(valid,run_id)
                    repo.save_actions(actions,run_id)
                    for issue in issues: repo.issue(series,run_id,issue)
                    if prefix_confirmed:
                        # A partial run may confirm its closed, gap-free PREFIX.
                        # Never confirm a provisional bar or move past the first missing bar.
                        repo.mark_covered(series,start,confirmed_end)
                        if summary["failed"]==0: repo.set_cursor(series,confirmed_end)
                    repo.finish_run(run_id,"success" if complete else "partial",count,
                                    received_start=min((b.timestamp for b in received),default=None),
                                    received_end=max((b.timestamp for b in received),default=None))
                    uow.commit()
                summary["records"]+=count
                if not complete: summary["failed"]+=1
            except Exception as exc:
                with self.uow_factory() as uow:
                    # Generic errors are sanitized; never log DB URLs, SQL params or secrets.
                    uow.repositories.finish_run(run_id,"failed",0,type(exc).__name__)
                    uow.commit()
                summary["failed"]+=1
                if isinstance(exc, ProviderError) and not isinstance(exc, TransientError): break
            start = finish
        return summary
