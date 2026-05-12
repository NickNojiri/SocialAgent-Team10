import asyncio
import httpx
import random
import time
import sys

# A large list of cities to generate random pairs
CITIES = [
    "New York, NY", "Los Angeles, CA", "Chicago, IL", "Houston, TX", 
    "Phoenix, AZ", "Philadelphia, PA", "San Antonio, TX", "San Diego, CA", 
    "Dallas, TX", "San Jose, CA", "Austin, TX", "Jacksonville, FL", 
    "Fort Worth, TX", "Columbus, OH", "San Francisco, CA", "Charlotte, NC", 
    "Indianapolis, IN", "Seattle, WA", "Denver, CO", "Washington, DC", 
    "Boston, MA", "El Paso, TX", "Nashville, TN", "Detroit, MI", 
    "Oklahoma City, OK", "Portland, OR", "Las Vegas, NV", "Memphis, TN", 
    "Louisville, KY", "Baltimore, MD", "Milwaukee, WI", "Albuquerque, NM", 
    "Tucson, AZ", "Fresno, CA", "Mesa, AZ", "Sacramento, CA", "Atlanta, GA", 
    "Kansas City, MO", "Colorado Springs, CO", "Miami, FL", "Raleigh, NC", 
    "Omaha, NE", "Long Beach, CA", "Virginia Beach, VA", "Oakland, CA", 
    "Minneapolis, MN", "Tulsa, OK", "Arlington, TX", "Tampa, FL", 
    "New Orleans, LA", "Wichita, KS", "Cleveland, OH", "Bakersfield, CA", 
    "Aurora, CO", "Anaheim, CA", "Honolulu, HI", "Santa Ana, CA", 
    "Riverside, CA", "Corpus Christi, TX", "Lexington, KY", "Stockton, CA", 
    "Henderson, NV", "St. Paul, MN", "St. Louis, MO", "Cincinnati, OH", 
    "Pittsburgh, PA", "Greensboro, NC", "Anchorage, AK", "Plano, TX", 
    "Lincoln, NE", "Orlando, FL", "Irvine, CA", "Newark, NJ", 
    "Toledo, OH", "Durham, NC", "Chula Vista, CA", "Fort Wayne, IN", 
    "Jersey City, NJ", "St. Petersburg, FL", "Laredo, TX", "Madison, WI", 
    "Chandler, AZ", "Buffalo, NY", "Lubbock, TX", "Scottsdale, AZ", 
    "Reno, NV", "Glendale, AZ", "Gilbert, AZ", "Winston-Salem, NC", 
    "North Las Vegas, NV", "Norfolk, VA", "Chesapeake, VA", "Garland, TX", 
    "Irving, TX", "Hialeah, FL", "Fremont, CA", "Boise, ID", "Richmond, VA", 
    "Baton Rouge, LA", "Spokane, WA", "Des Moines, IA"
]

LOG_FILE = "stress_test.log"

def log_print(msg):
    print(msg)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(msg + "\n")

async def geocode(client, city):
    """Hits Nominatim to get lat/lng."""
    url = "https://nominatim.openstreetmap.org/search"
    headers = {'User-Agent': 'SocialAgent-Team10-StressTest'}
    try:
        resp = await client.get(url, params={'q': city, 'format': 'json', 'limit': 1}, headers=headers, timeout=10.0)
        data = resp.json()
        if data:
            return float(data[0]['lat']), float(data[0]['lon'])
    except Exception as e:
        log_print(f"   [GEOCODE ERROR] Failed to geocode {city}: {e}")
    return None, None

async def overpass_venues(client, lat, lng, label="Centroid"):
    """Hits Overpass API with fallbacks and dynamic scaling."""
    headers = {'User-Agent': 'SocialAgent-Team10-StressTest'}
    
    for radius in [5000, 15000, 30000]:
        query = f'[out:json];(node["amenity"~"cafe|bar|restaurant"](around:{radius},{lat},{lng}););out 20;'
        try:
            resp = await client.post("https://overpass-api.de/api/interpreter", data={'data': query}, headers=headers, timeout=15.0)
            if resp.status_code == 200:
                data = resp.json()
                venues = []
                for el in data.get('elements', []):
                    tags = el.get('tags', {})
                    name = tags.get('name')
                    cuisine = tags.get('cuisine', '')
                    if name:
                        item = f"{name} ({cuisine.title()})" if cuisine else name
                        if item not in venues: venues.append(item)
                    if len(venues) >= 3: break
                
                if venues:
                    log_print(f"   [SUCCESS] Found venues near {label} at {radius}m radius!")
                    return venues
            
            # Fallback to Nominatim Search if Overpass fails or is empty
            nom_resp = await client.get(
                "https://nominatim.openstreetmap.org/search",
                params={'q': 'restaurant', 'format': 'json', 'lat': lat, 'lon': lng, 'limit': 3},
                headers=headers
            )
            if nom_resp.status_code == 200:
                venues = [item.get('display_name', '').split(',')[0] for item in nom_resp.json()]
                if venues:
                    log_print(f"   [SUCCESS] Found Nominatim fallbacks near {label}!")
                    return venues

        except Exception as e:
            log_print(f"   [ERROR] Search failed for {label} at {radius}m: {e}")
            continue

    return []

async def run_stress_test(iterations=100):
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        f.write("=== SOCIAL AGENT STRESS TEST START ===\n")
        
    log_print(f"Starting extreme location stress test for {iterations} iterations...")
    log_print("WARNING: This may take a long time due to Nominatim/Overpass rate limits.")
    
    success_count = 0
    fail_count = 0

    async with httpx.AsyncClient() as client:
        for i in range(1, iterations + 1):
            log_print(f"\n--- Test {i}/{iterations} ---")
            
            # 1. Pick two random cities
            city1, city2 = random.sample(CITIES, 2)
            log_print(f"User 1: {city1} | User 2: {city2}")
            
            # 2. Geocode (with respectful delays to not get banned)
            await asyncio.sleep(1.5) 
            lat1, lng1 = await geocode(client, city1)
            await asyncio.sleep(1.5)
            lat2, lng2 = await geocode(client, city2)
            
            if None in [lat1, lng1, lat2, lng2]:
                log_print("❌ Test Failed during Geocoding. Continuing fast...")
                fail_count += 1
                continue
                
            # 3. Calculate Centroid
            mid_lat = (lat1 + lat2) / 2
            mid_lng = (lng1 + lng2) / 2
            log_print(f"Raw Centroid: {mid_lat:.4f}, {mid_lng:.4f}")
            
            # 3.5 Snap Centroid to Nearest Populated Place (Reverse Geocoding)
            log_print("Finding nearest populated city to centroid...")
            await asyncio.sleep(1.5)
            reverse_url = "https://nominatim.openstreetmap.org/reverse"
            headers = {'User-Agent': 'SocialAgent-Team10-StressTest'}
            try:
                rev_resp = await client.get(
                    reverse_url, 
                    params={'lat': mid_lat, 'lon': mid_lng, 'format': 'json', 'zoom': 10}, 
                    headers=headers, timeout=10.0
                )
                rev_data = rev_resp.json()
                if 'error' not in rev_data and 'lat' in rev_data:
                    mid_lat = float(rev_data['lat'])
                    mid_lng = float(rev_data['lon'])
                    city_name = rev_data.get('name', 'Unknown Region')
                    log_print(f"Snapped to Nearest City: {city_name} ({mid_lat:.4f}, {mid_lng:.4f})")
                else:
                    log_print("Could not snap to city (too remote/ocean). Using raw centroid.")
            except Exception as e:
                log_print(f"Reverse geocode failed: {e}")

            # 4. Fetch real venues (with city fallbacks)
            log_print("Querying for venues (Centroid -> User 1 -> User 2)...")
            await asyncio.sleep(2.0)
            
            venues = await overpass_venues(client, mid_lat, mid_lng, "Centroid")
            if not venues:
                log_print("No venues at centroid. Falling back to User 1 City...")
                await asyncio.sleep(1.0)
                venues = await overpass_venues(client, lat1, lng1, city1)
            if not venues:
                log_print("No venues at User 1. Falling back to User 2 City...")
                await asyncio.sleep(1.0)
                venues = await overpass_venues(client, lat2, lng2, city2)
            
            # 5. Simulate the conversation
            if venues:
                v_str = "\n".join([f"  {idx+1}. {v}" for idx, v in enumerate(venues)])
                log_print(f"🤖 [Eve]: Midpoint strategy complete!")
                log_print(f"🤖 [Eve]: Here are highly-rated spots I found:\n{v_str}")
                log_print("✅ Test Passed.")
                success_count += 1
            else:
                log_print(f"🤖 [Eve]: I couldn't find ANY cafes or bars even after falling back to both cities.")
                log_print("❌ Test Failed (Extreme isolation).")
                fail_count += 1
                
    log_print("\n==================================")
    log_print(f"TEST COMPLETE.")
    log_print(f"Total Runs: {iterations}")
    log_print(f"Success: {success_count} | Fails: {fail_count}")
    log_print(f"Full log saved to {LOG_FILE}")
    log_print("==================================")

if __name__ == "__main__":
    # Reducing to 10 iterations as requested
    asyncio.run(run_stress_test(10))
