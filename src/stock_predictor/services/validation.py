import csv
import re
from stock_predictor.domain.models import DataQualityIssue, INTERVALS

def read_tickers(path):
    with open(path, newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != ["ticker"]: raise ValueError("CSV must have exactly ticker header")
        result = []
        for row in reader:
            ticker = row["ticker"].strip().upper()
            if not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,14}", ticker):
                raise ValueError(f"Invalid ticker: {ticker!r}")
            if ticker not in result: result.append(ticker)
        return result

def validate_bar(bar, calendar):
    issues = []
    prices = [bar.open, bar.high, bar.low, bar.close]
    if any(not p.is_finite() or p <= 0 for p in prices):
        issues.append(DataQualityIssue("price", "Nonpositive/nonfinite OHLC", bar.timestamp))
    elif not (bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high):
        issues.append(DataQualityIssue("ohlc", "Inconsistent OHLC", bar.timestamp))
    if bar.volume < 0: issues.append(DataQualityIssue("volume", "Negative volume", bar.timestamp))
    if bar.timestamp.tzinfo is None:
        issues.append(DataQualityIssue("timezone", "Naive timestamp", bar.timestamp))
    else:
        day, session, complete = calendar.classify(bar.timestamp, bar.series.interval)
        if day != bar.session_date or session != bar.series.session:
            issues.append(DataQualityIssue("session", "Incorrect session/date", bar.timestamp))
        if bar.series.interval != "1d" and session == "regular":
            opening, _ = calendar.bounds(day)
            seconds = (bar.timestamp-opening).total_seconds()
            if seconds % (INTERVALS[bar.series.interval]*60):
                issues.append(DataQualityIssue("interval", "Timestamp not aligned to session interval", bar.timestamp))
        if bar.complete and not complete:
            issues.append(DataQualityIssue("provisional", "Future or unfinished candle", bar.timestamp))
    return issues
