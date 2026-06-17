# recommend/Dockerfile — Phase 6 event-recommendation service
# Build context is the repo root (it needs the src/ package), e.g.:
#   docker build -f recommend/Dockerfile -t event-recommend .
FROM python:3.12-slim AS recommend

WORKDIR /app

# Install dependencies
COPY recommend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the ingestion package (serving app + sink + schemas). Only src/ingestion is
# needed; the recommend service does not import src/logic or src/services.
COPY src/ ./src/

EXPOSE 8003

CMD ["uvicorn", "src.ingestion.serving.app:app", "--host", "0.0.0.0", "--port", "8003"]
