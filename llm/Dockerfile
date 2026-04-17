# Stage 1: Build the bot app
FROM python:3.12-slim AS app

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create persistent data directory
RUN mkdir -p /data

# Copy source
COPY . .

# Expose port
EXPOSE 8002

# Run FastAPI
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8002"]