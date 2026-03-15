import requests
import time

def address_to_coords(address):
    """Turns a string address into Lat/Lng (Free via Nominatim)."""
    url = "https://nominatim.openstreetmap.org/search"
    params = {'q': address, 'format': 'json', 'limit': 1}
    headers = {'User-Agent': 'SocialAgent-Team10-StudentProject'}
    
    try:
        # Nominatim asks for 1 second between requests to stay free
        time.sleep(1) 
        response = requests.get(url, params=params, headers=headers)
        data = response.json()
        if data:
            return float(data[0]['lat']), float(data[0]['lon'])
    except Exception as e:
        print(f"Geocoding error: {e}")
    return None, None

def get_midpoint(lat1, lon1, lat2, lon2):
    """Calculates the average Lat/Lng between two points."""
    return (lat1 + lat2) / 2, (lon1 + lon2) / 2

def find_venues(lat, lng, place_type="cafe"):
    """Finds venues via OpenStreetMap (Overpass API)."""
    query = f"""
    [out:json];
    node["amenity"="{place_type}"](around:1500,{lat},{lng});
    out body;
    """
    url = "https://overpass-api.de/api/interpreter"
    
    try:
        response = requests.post(url, data={'data': query}, timeout=10)
        data = response.json()
        venues = []
        for element in data.get('elements', [])[:5]:
            tags = element.get('tags', {})
            venues.append({
                "name": tags.get('name', "Unnamed Venue"),
                "rating": "Community Verified (OSM)",
                "address": f"Lat: {element.get('lat')}, Lng: {element.get('lon')}"
            })
        return venues
    except Exception as e:
        print(f"Venue search error: {e}")
        return []