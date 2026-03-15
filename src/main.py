from fastapi import FastAPI
from langchain_community.llms import Ollama

app = FastAPI()
llm = Ollama(model="llama3.1:8b")

@app.get("/")
def home():
    return {"message": "Team 10 AI Agent is Online"}

@app.get("/ask")
def ask_ai(prompt: str):
    response = llm.invoke(prompt)
    return {"bot": response}