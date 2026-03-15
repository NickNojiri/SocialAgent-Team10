import os
from dotenv import load_dotenv
from src.logic.parser import TimeParser
from src.logic.scheduler import MeetingScheduler

# --- TOGGLE SERVICE HERE ---
import src.services.location_service as service  # FREE (OSM)
# import src.services.google_maps as service     # PREMIUM (Google)
# ---------------------------

load_dotenv()

def run_integration_test():
    print(f"🚀 Starting Team 10 Integration Test using: {service.__name__}\n")

    # 1. Parse Time (Nicholas)
    parser = TimeParser()
    clean_time = parser.extract_time("this Friday at 7pm") 
    
    # 2. Get Coordinates and Midpoint (John)
    # We'll calculate the middle ground between two iconic spots
    lat1, lon1 = service.address_to_coords("Empire State Building, NY")
    lat2, lon2 = service.address_to_coords("Brooklyn Bridge, NY")
    
    if lat1 and lat2:
        mlat, mlng = service.get_midpoint(lat1, lon1, lat2, lon2)
        print(f"📍 Person A: {lat1}, {lon1}")
        print(f"📍 Person B: {lat2}, {lon2}")
        print(f"✅ Midpoint: {mlat}, {mlng}")
    else:
        print("⚠️ Geocoding failed, using default coordinates.")
        mlat, mlng = 40.7128, -74.0060
    
    # 3. Get Venues (Service Agnostic)
    # Note: We no longer pass api_key here because the 'service' alias handles it internally
    venues = service.find_venues(mlat, mlng, place_type="bar")

    # 4. Run Scheduler (Alfredo + LLM)
    scheduler = MeetingScheduler()
    final_plan = scheduler.negotiate_plan(
        group_chat_summary=f"The group wants to meet around {clean_time}. Alfredo hates loud music.",
        available_venues=str(venues)
    )
    
    print(f"\n✅ Time Extracted: {clean_time}")
    print(f"✅ Venues Found: {len(venues)}")
    print("-" * 30)
    print(f"🤖 AI PLAN:\n{final_plan}")
    print("-" * 30)

if __name__ == "__main__":
    run_integration_test()