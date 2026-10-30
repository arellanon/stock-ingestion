from dataclasses import asdict, replace
from datetime import timedelta, UTC
from decimal import Decimal
import itertools
from stock_predictor.domain.models import MarketBar, INTERVALS

def record(bar):
    result = asdict(bar)
    result.update(result.pop("series"))
    result["session_time"] = bar.timestamp.astimezone(UTC).time().replace(tzinfo=None).isoformat()
    for key in ("open","high","low","close"): result[key] = str(result[key])
    return result

class QueryService:
    def __init__(self,uow_factory): self.uow_factory = uow_factory
    def bars(self,series,start,end,batch_size=1000):
        with self.uow_factory() as uow:
            yield from uow.repositories.get_bars(series,start,end,batch_size)
    def dataframe(self,series,start,end):
        """Explicit full-memory convenience API. Prefer export for large histories."""
        import pandas as pd
        frame = pd.DataFrame(record(b) for b in self.bars(series,start,end))
        if not frame.empty:
            frame["timestamp"] = pd.to_datetime(frame.timestamp,utc=True)
            frame = frame.sort_values("timestamp")
        frame.attrs.update(series=asdict(series),timezone="UTC",prices="decimal strings")
        return frame
    def latest(self,series,start,end):
        with self.uow_factory() as uow:
            return uow.repositories.get_latest_bar(series,start,end)
    def potential_gaps(self,series,start,end,calendar):
        if series.session != "regular": raise ValueError("Extended expectations require an explicit provider schedule")
        actual = iter(self.bars(series,start,end))
        current = next(actual,None)
        for stamp in calendar.expected(start,end,series.interval):
            if not calendar.classify(stamp,series.interval)[2]: continue
            while current is not None and current.timestamp<stamp: current = next(actual,None)
            if current is None or current.timestamp!=stamp: yield stamp
    def compare(self,left,right,start,end):
        if (left.instrument_id,left.interval,left.adjustment,left.session)!=(right.instrument_id,right.interval,right.adjustment,right.session):
            raise ValueError("Compare equivalent instrument/interval/adjustment/session policies")
        a,b = iter(self.bars(left,start,end)),iter(self.bars(right,start,end))
        x,y = next(a,None),next(b,None)
        while x is not None and y is not None:
            if x.timestamp<y.timestamp: x = next(a,None)
            elif y.timestamp<x.timestamp: y = next(b,None)
            else:
                yield {"timestamp":x.timestamp,"left":left,"right":right,
                       "close_difference":x.close-y.close,"volume_difference":x.volume-y.volume}
                x,y = next(a,None),next(b,None)
    def actions(self,instrument_id,provider):
        with self.uow_factory() as uow: return uow.repositories.actions(instrument_id,provider)
    def export(self,series,start,end,path,format="parquet",batch_size=1000):
        import csv
        import json
        import pyarrow as pa
        import pyarrow.parquet as pq
        if format not in {"parquet","csv"}: raise ValueError("Format must be csv or parquet")
        columns = ["instrument_id","provider","feed","interval","adjustment","session","timestamp","session_date","session_time","open","high","low","close","volume","complete","obtained_at","provenance"]
        schema = pa.schema([(k,pa.int64() if k=="volume" else pa.bool_() if k=="complete" else pa.string()) for k in columns])
        iterator = iter(self.bars(series,start,end,batch_size))
        count = 0
        with open(path,"w",newline="",encoding="utf-8") if format=="csv" else pq.ParquetWriter(path,schema) as output:
            writer = csv.DictWriter(output,fieldnames=columns) if format=="csv" else None
            if writer: writer.writeheader()
            while batch := list(itertools.islice(iterator,batch_size)):
                rows = []
                for bar in batch:
                    row = record(bar)
                    row["timestamp"] = bar.timestamp.isoformat()
                    row["obtained_at"] = bar.obtained_at.isoformat()
                    row["session_date"] = bar.session_date.isoformat()
                    row["provenance"] = json.dumps(row["provenance"],sort_keys=True)
                    rows.append(row)
                if writer: writer.writerows(rows)
                else: output.write_table(pa.Table.from_pylist(rows,schema=schema))
                count += len(rows)
        return count

def aggregate(bars, target_interval, calendar):
    """Under-demand regular-session aggregation; only full, contiguous source buckets.

    Missing constituents suppress a derived candle. Closing short buckets are allowed.
    Input must be ordered, complete, and from a single regular-session series.
    """
    if target_interval == "1d": raise ValueError("Daily aggregation is not implemented")
    target = INTERVALS[target_interval]
    bucket, values, source = None, [], None
    def emit():
        if not values: return None
        step = INTERVALS[source.interval]
        opening,closing = calendar.bounds(values[0].session_date)
        end = min(bucket+timedelta(minutes=target),closing)
        expected = []
        stamp = bucket
        while stamp<end:
            expected.append(stamp)
            stamp += timedelta(minutes=step)
        if [b.timestamp for b in values]!=expected or not all(b.complete for b in values): return None
        return MarketBar(replace(source,interval=target_interval),bucket,values[0].session_date,
            values[0].open,max(b.high for b in values),min(b.low for b in values),values[-1].close,
            sum(b.volume for b in values),True,provenance={"derived":True,"source_interval":source.interval,"source_timestamps":[b.timestamp.isoformat() for b in values]})
    previous = None
    for bar in bars:
        if source is None: source = bar.series
        if bar.series!=source or source.session!="regular": raise ValueError("Require a single regular series")
        step = INTERVALS[source.interval]
        if target<=step or target%step: raise ValueError("Target must be a larger multiple of source")
        if previous and bar.timestamp<=previous: raise ValueError("Require strictly increasing timestamps")
        previous = bar.timestamp
        opening,closing = calendar.bounds(bar.session_date)
        offset = int((bar.timestamp-opening).total_seconds()//60)
        new_bucket = opening+timedelta(minutes=(offset//target)*target)
        if new_bucket!=bucket:
            derived = emit()
            if derived: yield derived
            bucket,values = new_bucket,[]
        values.append(bar)
    derived = emit()
    if derived: yield derived
