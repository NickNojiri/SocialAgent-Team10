import asyncio
import os
import json
import httpx
from fastapi.testclient import TestClient

# Mock env vars before importing apps
os.environ["DB_URL"] = "http://localhost:8002"
os.environ["LLM_URL"] = "http://localhost:8001"
os.environ["OLLAMA_MODEL"] = "gemma4"

from db.app import app as db_app, init_db, get_conn
from llm.app import app as llm_app
import llm.app as llm_module

# Re-init DB in memory or specific test file
os.environ["IS_DOCKER"] = ""
llm_module.DB_PATH = "./data/test_events.db"
import db.app as db_module
db_module.DB_PATH = "./data/test_events.db"
db_module.init_db()

db_client = TestClient(db_app)
llm_client = TestClient(llm_app)

OriginalAsyncClient = httpx.AsyncClient

# --- Mocking the LLM ---
class MockAsyncClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self): return self
    async def __aexit__(self, exc_type, exc_val, exc_tb): pass

    async def get(self, url, *args, **kwargs):
        # Route DB requests from LLM app directly to our db_client
        if url.startswith("http://localhost:8002"):
            path = url.replace("http://localhost:8002", "")
            resp = db_client.get(path)
            class MockResp:
                status_code = resp.status_code
                def json(self): return resp.json()
            return MockResp()
        return None

    async def post(self, url, json=None, data=None, *args, **kwargs):
        if url.startswith("http://localhost:8002"):
            path = url.replace("http://localhost:8002", "")
            resp = db_client.post(path, json=json)
            class MockResp:
                status_code = resp.status_code
                text = resp.text
                def json(self): return resp.json()
            return MockResp()
        if url.startswith("https://overpass-api.de/api/interpreter"):
            class MockResp:
                status_code = 200
                def json(self):
                    return {"elements": [
                        {"tags": {"name": "Starbucks", "cuisine": "coffee_shop"}},
                        {"tags": {"name": "Local Cafe", "cuisine": "breakfast"}},
                        {"tags": {"name": "The Brewery", "cuisine": "pizza"}}
                    ]}
            return MockResp()
            
        # Pass through to real httpx for any other APIs
        async with OriginalAsyncClient(timeout=10.0) as real_client:
            return await real_client.post(url, json=json, data=data, *args, **kwargs)


async def mock_call_ollama(client, messages):
    """Simulate Gemma/Ollama understanding locations and picking a spot."""
    # Check if locations were injected in the system prompt
    sys_prompt = messages[0]["content"]
    user_msg = messages[-1]["content"]
    
    if "REAL VENUES NEAR EXACT MIDPOINT" in sys_prompt:
        return (
            "Since Nick is in Long Beach and John is in Torrance, the exact midpoint is near Carson! "
            "Here are 3 highly-rated spots near the midpoint:\n"
            "1. Starbucks (Coffee Shop)\n"
            "2. Local Cafe (Breakfast)\n"
            "3. The Brewery (Pizza)\n"
            "Which one looks good?"
        )
    else:
        return (
            "I'd love to schedule that. Where is everyone located so I can find a fair spot?\n"
            # No action block because we need more info
        )

# Replace the real call_ollama with our mock
llm_module.call_ollama = mock_call_ollama
llm_module.httpx.AsyncClient = MockAsyncClient

def run_integration_test():
    print("=== Integration Test Started ===\n")
    guild_id = "guild123"
    channel_id = "chan123"
    
    # 1. Clean DB
    conn = db_module.get_conn()
    conn.execute("DELETE FROM messages")
    conn.execute("DELETE FROM user_locations")
    conn.execute("DELETE FROM events")
    conn.commit()
    conn.close()

    # 2. Add locations to DB (simulating /setlocation)
    print("📍 [Step 1] Setting user locations via DB API...")
    db_client.post("/locations", json={
        "user_id": "u1", "guild_id": guild_id, "display_name": "Nick", "location_text": "Long Beach, CA", "lat": 33.7701, "lng": -118.1937
    })
    db_client.post("/locations", json={
        "user_id": "u2", "guild_id": guild_id, "display_name": "John", "location_text": "Torrance, CA", "lat": 33.8358, "lng": -118.3406
    })
    print("   -> Success.\n")

    # 3. Simulate Chat Request
    print("💬 [Step 2] User asks to schedule an event...")
    chat_payload = {
        "channel_id": channel_id,
        "guild_id": guild_id,
        "user_id": "u1",
        "username": "Nick",
        "message": "Let's plan a game night for Saturday!"
    }
    
    resp = llm_client.post("/chat", json=chat_payload)
    data = resp.json()
    
    print(f"🤖 [Eve]: {data['response']}")
    print(f"🔔 [Action parsed]: {data['action'] != None}")
    if data['action']:
        print(f"   Location Chosen: {data['action']['location']}")
        print(f"   Event Name: {data['action']['name']}")
        print(f"   Start Time: {data['action']['start_time']}")
        
    print("\n💾 [Step 3] Verifying Database persistence...")
    events = db_client.get(f"/events/{guild_id}").json()
    if len(events) > 0:
        print(f"   -> Event successfully saved in DB! ID: {events[0]['id']}, Name: {events[0]['name']}")
    else:
        print("   -> ERROR: Event not saved in DB.")
        
    print("\n=== Integration Test Complete ===")

if __name__ == "__main__":
    run_integration_test()
