import asyncio
import httpx
import uuid

LLM_URL = "http://localhost:8001"
DB_URL = "http://localhost:8002"

async def main():
    print("==================================================")
    print("📱 SocialAgent Local Testing CLI (No Discord)")
    print("==================================================")
    print("Make sure the backend is running! You can start it with:")
    print("   docker-compose up -d db llm")
    print("--------------------------------------------------")
    
    # Generate fake IDs for testing
    guild_id = "test-guild-" + str(uuid.uuid4())[:8]
    channel_id = "test-channel-" + str(uuid.uuid4())[:8]
    user_id = "test-user-123"
    username = "TestUser"
    
    print(f"Testing as user: {username} in virtual channel: {channel_id}\n")
    print("Commands:")
    print("  /setlocation <city>  - Set your location (simulates discord slash command)")
    print("  /quit                - Exit the CLI")
    print("--------------------------------------------------\n")

    async with httpx.AsyncClient(timeout=300.0) as client:
        while True:
            try:
                user_input = input(f"[{username}]: ")
            except (EOFError, KeyboardInterrupt):
                break
                
            if not user_input.strip():
                continue
                
            if user_input.lower() in ["/quit", "/exit"]:
                break
                
            if user_input.lower().startswith("/setlocation "):
                location = user_input[13:].strip()
                try:
                    resp = await client.post(f"{DB_URL}/locations", json={
                        "user_id": user_id,
                        "guild_id": guild_id,
                        "display_name": username,
                        "location_text": location,
                    })
                    resp.raise_for_status()
                    print(f"📍 [System]: Location saved! ({location})")
                except Exception as e:
                    print(f"⚠️ [System]: Failed to save location: {e}")
                continue
            
            # Send message to LLM service
            payload = {
                "channel_id": channel_id,
                "guild_id": guild_id,
                "user_id": user_id,
                "username": username,
                "message": user_input,
            }
            
            print("⏳ Thinking...")
            try:
                resp = await client.post(f"{LLM_URL}/chat", json=payload)
                resp.raise_for_status()
                data = resp.json()
                
                print(f"\n[Eve]: {data.get('response', '')}")
                
                action = data.get("action")
                if action:
                    print(f"\n🔔 [System]: ACTION TRIGGERED -> {action}")
                    print(f"📅 Simulated Discord Event: '{action.get('name')}' at {action.get('location')} ({action.get('start_time')})")
                    
                print("-" * 50)
            except Exception as e:
                print(f"\n⚠️ [System Error]: Could not reach LLM service at {LLM_URL}. Is it running?")
                print(f"Details: {e}")
                print("-" * 50)

if __name__ == "__main__":
    asyncio.run(main())
