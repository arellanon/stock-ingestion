from datetime import datetime, timedelta, UTC
from zoneinfo import ZoneInfo
import exchange_calendars as xc
import pandas as pd
from stock_predictor.domain.models import INTERVALS
NY = ZoneInfo("America/New_York")

class Calendar:
    def __init__(self):
        self.calendar = xc.get_calendar("XNYS", start="1990-01-01", end="2040-12-31")
    def bounds(self, day):
        label = pd.Timestamp(day)
        if not self.calendar.is_session(label): return None
        return (self.calendar.session_open(label).to_pydatetime(),
                self.calendar.session_close(label).to_pydatetime())
    def classify(self, stamp, interval, now=None):
        if stamp.tzinfo is None: raise ValueError("Naive timestamp")
        day = stamp.astimezone(NY).date()
        bounds = self.bounds(day)
        if bounds is None: return day, "closed", False
        opening, closing = bounds
        if interval == "1d": return day, "regular", closing <= (now or datetime.now(UTC))
        local = stamp.astimezone(NY)
        session = "regular" if opening <= stamp < closing else "extended"
        if session == "extended" and not (4 <= local.hour < 20):
            return day, "closed", False
        finish = min(stamp + timedelta(minutes=INTERVALS[interval]), closing) if session == "regular" else stamp + timedelta(minutes=INTERVALS[interval])
        return day, session, finish <= (now or datetime.now(UTC))
    def expected(self, start, end, interval):
        sessions = self.calendar.sessions_in_range(pd.Timestamp(start.astimezone(NY).date()),
                                                   pd.Timestamp(end.astimezone(NY).date()))
        for label in sessions:
            opening, closing = self.bounds(label.date())
            if interval == "1d":
                stamp = datetime.combine(label.date(), datetime.min.time(), NY).astimezone(UTC)
                if start <= stamp < end: yield stamp
            else:
                stamp = opening
                while stamp < closing:
                    if start <= stamp < end: yield stamp
                    stamp += timedelta(minutes=INTERVALS[interval])
