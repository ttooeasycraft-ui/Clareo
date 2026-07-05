## Root-level Dockerfile used by Railway when Root Directory is NOT set to backend/.
## If Root Directory IS set to "backend/" in the Railway dashboard, this file is
## ignored — Railway uses backend/Dockerfile directly.

FROM python:3.11-slim

# System deps
RUN apt-get update && apt-get install -y \
    ffmpeg \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Upgrade pip and install build tools
RUN pip install --no-cache-dir --upgrade pip "setuptools<81.0.0" wheel

# Install openai-whisper separately with --no-build-isolation so it can find
# pkg_resources from the global setuptools (PEP 517 isolated envs don't see it)
RUN pip install --no-cache-dir --no-build-isolation openai-whisper==20240930

# Install remaining Python deps (copy from backend/ sub-path)
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source
COPY backend/ .

# Confirm ffmpeg is available
RUN ffmpeg -version 2>&1 | head -1

RUN mkdir -p jobs

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
