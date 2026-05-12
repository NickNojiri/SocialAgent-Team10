import asyncio
import httpx

async def test_real_osm():
    print("🌍 Reaching out to real OpenStreetMap Overpass API...")
    
    # Coordinates halfway between Long Beach and Torrance
    avg_lat = 33.8029
    avg_lng = -118.2672
    
    overpass_query = f"""
    [out:json];
    (
      node["amenity"="cafe"](around:5000,{avg_lat},{avg_lng});
      node["amenity"="bar"](around:5000,{avg_lat},{avg_lng});
      node["amenity"="restaurant"](around:5000,{avg_lat},{avg_lng});
    );
    out 20;
    """
    
    print(f"📍 Searching 5000m radius around Centroid: {avg_lat}, {avg_lng}\n")
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://overpass-api.de/api/interpreter", 
            data={'data': overpass_query},
            headers={'User-Agent': 'SocialAgent-Team10-StudentProject'}
        )
        print("Response status:", resp.status_code)
        if resp.status_code != 200:
            print("Response text:", resp.text)
        data = resp.json()
        
        venues = []
        for el in data.get('elements', []):
            tags = el.get('tags', {})
            name = tags.get('name')
            cuisine = tags.get('cuisine', '')
            if name:
                label = f"{name} ({cuisine})" if cuisine else name
                venues.append(label)
                
        print("🍔 REAL RESTAURANTS FOUND ON THE MAP:")
        for v in venues:
            print(f" - {v}")
            
if __name__ == "__main__":
    asyncio.run(test_real_osm())
