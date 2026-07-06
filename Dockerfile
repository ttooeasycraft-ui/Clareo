## Root-level Dockerfile — used by Railway when Root Directory is repo root (not backend/).

FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    ffmpeg \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# faster-whisper installs cleanly — no setuptools pinning needed
RUN pip install --no-cache-dir --upgrade pip
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .
RUN mkdir -p jobs

EXPOSE 8000

# Shell form so $PORT env var is expanded by Railway at runtime
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
