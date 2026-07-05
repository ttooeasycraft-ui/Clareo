## Root-level Dockerfile — used by Railway when Root Directory is repo root (not backend/).

FROM python:3.11-slim

# System deps
RUN apt-get update && apt-get install -y \
    ffmpeg \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Pin setuptools<81 — setuptools 81+ broke pkg_resources in legacy setup.py
# builds (openai-whisper==20240930 still uses setup.py + pkg_resources).
# Must happen BEFORE installing whisper.
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir "setuptools<81" wheel

# Install whisper with --no-build-isolation so it uses the pinned setuptools above
RUN pip install --no-cache-dir --no-build-isolation "openai-whisper==20240930"

# Install the rest of the deps
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source
COPY backend/ .

# Confirm ffmpeg is available
RUN ffmpeg -version 2>&1 | head -1

RUN mkdir -p jobs

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
