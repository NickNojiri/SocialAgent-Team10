# SpotBot developer shortcuts
setup:
	python3 -m venv .venv
	. .venv/bin/activate && pip install --upgrade pip
	. .venv/bin/activate && pip install -r requirements.txt
	. .venv/bin/activate && python -m playwright install chromium
	@echo "Now: ollama serve (separate tab), then: ollama pull llama3.1:8b && ollama pull llama3.2:1b"

test:
	pytest -k "not live" -q

serve:
	ollama serve

ingest:
	python -m src.ingestion.cli

admin:
	uvicorn src.ingestion.serving.admin:app --port 8010

recommend:
	uvicorn src.ingestion.serving.app:app --port 8003

bot:
	python app/bot.py

smoke:
	python scripts/smoke_ingest.py
