# admin/Dockerfile — the capture + catalog app (:8010) for staging (#11).
#
# Until staging, this app ran on the host because it drives Chromium and Whisper.
# Here it gets both in the image, so the whole stack is one `docker compose up`.
# Build context is the repo root:
#   docker build -f admin/Dockerfile -t spotbot-admin .
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    # Chromium goes somewhere every user can read (the app doesn't run as root).
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    # Whisper's model downloads land on the data volume, so a rebuild doesn't refetch them.
    HF_HOME=/app/data/.cache/huggingface

# ffmpeg: faster-whisper decodes the reel's audio with it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-ingestion.txt admin/requirements.txt ./
RUN pip install -r requirements-ingestion.txt -r requirements.txt \
    && python -m playwright install --with-deps chromium

COPY src/ ./src/

# Not root: this process opens untrusted web pages and media files.
RUN useradd --create-home --uid 10001 spotbot \
    && mkdir -p /app/data \
    && chown -R spotbot /app/data
USER spotbot

EXPOSE 8010
CMD ["uvicorn", "src.ingestion.serving.admin:app", "--host", "0.0.0.0", "--port", "8010"]
