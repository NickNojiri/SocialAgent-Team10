import dateparser
import datetime
from dateparser.date import DateDataParser

class TimeParser:
    def __init__(self):
        pass

    def parse_datetime(self, text, relative_base=None, tz="America/Los_Angeles"):
        """Machine-readable counterpart to extract_time (added for Phase 4 ingestion).

        Returns (utc_datetime, period) where period is dateparser's granularity
        ('time' | 'day' | 'week' | 'month' | 'year'), or (None, None) if unparseable.
        Naive inputs are assumed to be in `tz` and converted to UTC; relative
        expressions ("this Friday") resolve against `relative_base`.
        extract_time() below is intentionally left unchanged.
        """
        settings = {
            "PREFER_DATES_FROM": "future",
            "RETURN_AS_TIMEZONE_AWARE": True,
            "RETURN_TIME_AS_PERIOD": True,  # period == 'time' when a time-of-day is present
            "TIMEZONE": tz,
            "TO_TIMEZONE": "UTC",
        }
        if relative_base is not None:
            settings["RELATIVE_BASE"] = relative_base

        try:
            result = DateDataParser(settings=settings).get_date_data(text)
        except Exception:
            return None, None

        if result and result.date_obj is not None:
            return result.date_obj, result.period
        return None, None

    def extract_time(self, text: str):
        # 1. Get current time
        now = datetime.datetime.now()
        
        # 2. Simplified settings (Removed the manual PARSERS list)
        settings = {
            'PREFER_DATES_FROM': 'future',
            'RELATIVE_BASE': now
        }
        
        # 3. Try to parse
        parsed_date = dateparser.parse(text, settings=settings)
        
        # 4. Success check
        if parsed_date:
            return parsed_date.strftime("%A, %B %d at %I:%M %p")
        
        # 5. Fallback: If it returns None, pass the raw text to the AI
        # This prevents the 'None' error in the AI Plan!
        return f"around {text}"