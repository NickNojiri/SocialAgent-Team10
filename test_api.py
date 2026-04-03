import pytest
from fastapi.testclient import TestClient
from src.main import app

client = TestClient(app)

def test_home():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"message": "Team 10 AI Agent is Online"}

def test_coordinate_validation():
    # Fails min_length=1 on user_ids
    response = client.post("/v1/coordinate", json={"user_ids": [], "raw_input": "meets this friday!"})
    assert response.status_code == 422 
    assert "too_short" in str(response.text)
    assert "user_ids" in str(response.text)

    # Fails min_length=5 on raw_input
    response = client.post("/v1/coordinate", json={"user_ids": ["john_123"], "raw_input": "hi"})
    assert response.status_code == 422
    assert "too_short" in str(response.text)
    assert "raw_input" in str(response.text)
