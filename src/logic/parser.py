import dateparser
import datetime

class TimeParser:
    def __init__(self):
        pass

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