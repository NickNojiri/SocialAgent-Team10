import os
import src.services.location_service as service  # FREE (OSM)
from src.logic.parser import TimeParser
from src.logic.scheduler import MeetingScheduler

class CoordinationAgent:
    def __init__(self):
        self.parser = TimeParser()
        self.scheduler = MeetingScheduler()
        
    def coordinate_meeting(self, user_ids: list, raw_input: str):
        # 1. Parse Time
        clean_time = self.parser.extract_time(raw_input)
        
        # 2. Get Coordinates and Midpoint from Database Metadata
        from src.logic.database import PreferenceStore
        store = PreferenceStore()
        coords = []
        for uid in user_ids:
            lat, lng = store.get_user_location(uid)
            if lat and lng:
                coords.append((lat, lng))
                
        if len(coords) >= 2:
            # calculate dynamic midpoint of N users
            mlat = sum(c[0] for c in coords) / len(coords)
            mlng = sum(c[1] for c in coords) / len(coords)
        elif len(coords) == 1:
            mlat, mlng = coords[0]
        else:
            # Fallback coordinates if no users contain valid metadata
            mlat, mlng = 40.7128, -74.0060
            
        # 3. Get Venues (Dead-Zone Protocol)
        venues = []
        for r in [500, 1000, 5000]:
            venues = service.find_venues(mlat, mlng, place_type="bar", radius=r)
            if venues:
                break
        
        # 4. Prompt Scheduler via LangChain
        final_plan = self.scheduler.negotiate_plan(
            group_chat_summary=raw_input,
            available_venues=str(venues)
        )
        
        return {
            "time_extracted": clean_time,
            "midpoint": [mlat, mlng],
            "venues_found": len(venues),
            "ai_plan": final_plan
        }
