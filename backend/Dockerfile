# VoiceKit SaaS Backend
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# PORT is injected by Render / Cloud Run — default to 8001 for local Docker use
EXPOSE ${PORT:-8001}

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8001}/gemini-live/health || exit 1

CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8001}
