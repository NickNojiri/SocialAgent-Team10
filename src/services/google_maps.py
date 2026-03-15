import requests
import os
# IMPORTANT:::to use swap names with location service and add api key

def find_midpoint_venues(lat, lng, api_key=None, place_type="bar"):
    # 1. Safety Check: Use the passed key or grab from environment
    key = api_key or os.getenv("GOOGLE_API_KEY")
    
    # 2. Mock Data Fallback: 
    # If no key is found, return fake data so the team can still test the AI.
    if not key or key == "your_actual_key_here":
        print("⚠️ No Google API Key found. Using mock data for testing...")
        return [
            {"name": "The Library Lounge", "rating": 4.8, "price_level": 2, "vicinity": "123 Quiet St"},
            {"name": "Turbo Nightclub", "rating": 4.1, "price_level": 3, "vicinity": "456 Loud Ave"},
            {"name": "Standard Cafe", "rating": 3.9, "price_level": 1, "vicinity": "789 Main St"}
        ]

    # 3. Real API Call
    url = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
    params = {
        "location": f"{lat},{lng}",
        "radius": 2000,  # Expanded to 2km for better results
        "type": place_type,
        "key": key
    }
    
    try:
        response = requests.get(url, params=params)
        response.raise_for_status() # Check for HTTP errors
        data = response.json()
        results = data.get("results", [])
        
        # 4. Return enriched data so the AI can judge "Vibe"
        venues = []
        for p in results[:5]: # Take top 5
            venues.append({
                "name": p.get("name"),
                "rating": p.get("rating"),
                "user_ratings_total": p.get("user_ratings_total"),
                "price_level": p.get("price_level"), # 1 (cheap) to 4 (expensive)
                "address": p.get("vicinity")
            })
        return venues

    except Exception as e:
        print(f"❌ Google API Error: {e}")
        return []