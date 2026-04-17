# Stage 1: Build the bot app
FROM python:3.12-slim AS app

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY . .

# Run the bot
CMD ["python", "bot.py"]