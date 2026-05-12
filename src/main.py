from fastapi import FastAPI
from pydantic import BaseModel, Field
from typing import List
from src.services.llm_provider import LlamaProvider
from src.logic.coordinator import CoordinationAgent

app = FastAPI()
llm = LlamaProvider.get_model()
agent = CoordinationAgent()

class CoordinateRequest(BaseModel):
    user_ids: List[str] = Field(..., min_length=1, description="List must contain at least 1 user_id")
    raw_input: str = Field(..., min_length=5, description="Input query must be at least 5 characters")

@app.get("/")
def home():
    return {"message": "Team 10 AI Agent is Online"}

@app.post("/v1/coordinate")
def coordinate_route(request: CoordinateRequest):
    result = agent.coordinate_meeting(
        user_ids=request.user_ids, 
        raw_input=request.raw_input
    )
    return {"meeting_plan": result}