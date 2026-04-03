import os
from dotenv import load_dotenv
from src.logic.coordinator import CoordinationAgent
from src.logic.database import PreferenceStore

load_dotenv()

def run_integration_test():
    print(f"🚀 Starting Team 10 Integration Test via CoordinationAgent\n")
    
    # 1. Inject Pearsona Data via ChromaDB Upsert for the LLM to scrape context
    store = PreferenceStore()
    store.upsert_preference("alfredo_123", "Alfredo strongly prefers quiet places with no loud music. He also likes tacos.", lat=40.7484, lng=-73.9857)
    store.upsert_preference("john_456", "John loves lively bars and Friday nights out.", lat=40.7061, lng=-73.9969)
    print(f"🧠 Updated ChromaDB user personas memory.")
    
    # 2. Initialize Agent and process the test coordination request
    agent = CoordinationAgent()
    result = agent.coordinate_meeting(
        user_ids=["alfredo_123", "john_456"], 
        raw_input="The group wants to meet around this Friday at 7pm. Alfredo hates loud music."
    )
    
    # 3. Print Results
    print(f"\n✅ Time Extracted: {result['time_extracted']}")
    print(f"✅ Midpoint: {result['midpoint']}")
    print(f"✅ Venues Found: {result['venues_found']}")
    print("-" * 30)
    print(f"🤖 AI PLAN:\n{result['ai_plan']}")
    print("-" * 30)

if __name__ == "__main__":
    run_integration_test()