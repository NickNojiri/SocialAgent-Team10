from src.ingestion.serving.recommender import Recommendation
from src.ingestion.serving.discord_format import format_as_embed

def test_format_as_embed():
    rec1 = Recommendation(
        content_hash="1",
        venue_name="Test Cafe",
        category="cafe_dessert",
        core_theme="Good coffee",
        source_url="http://example.com/1",
        distance=0.1,
        start_epoch=1700000000,
        end_epoch=1700003600
    )
    rec2 = Recommendation(
        content_hash="2",
        venue_name="Test Park",
        category="outdoors",
        core_theme="Nice walk",
        source_url="",
        distance=0.2,
        start_epoch=None,
        end_epoch=None
    )
    
    embed = format_as_embed([rec1, rec2])
    
    assert embed["title"] == "2 spots that match"
    assert "fields" in embed
    assert len(embed["fields"]) == 2
    
    f1 = embed["fields"][0]
    assert "🍰" in f1["name"]
    assert "Test Cafe" in f1["name"]
    assert "Cafés & dessert" in f1["name"] and "*" not in f1["name"]
    assert "Good coffee" in f1["value"]
    assert "1700000000" in f1["value"]
    assert "1700003600" in f1["value"]
    assert "http://example.com/1" in f1["value"]
    
    f2 = embed["fields"][1]
    assert "🏞️" in f2["name"]
    assert "Test Park" in f2["name"]
    assert "TBD" not in f2["value"] and "<t:" not in f2["value"]     # no date: no line about it

def test_format_as_embed_empty():
    embed = format_as_embed([])
    assert embed["title"] == "No Recommendations"
    assert "couldn't find" in embed["description"]
