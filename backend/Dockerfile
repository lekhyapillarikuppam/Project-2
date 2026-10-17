# Backend image for Render. Build from the backend/ folder.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Piper voices are only needed with TTS_FALLBACK=piper. Render runs edge-only (D26), so they are
# left out by default; build with --build-arg INCLUDE_PIPER=true to bundle them. The app finds them
# at models/<voice>.onnx relative to /app, which is the PIPER_MODEL_PATH default.
ARG INCLUDE_PIPER=false
RUN if [ "$INCLUDE_PIPER" = "true" ]; then \
        python -m piper.download_voices --download-dir /app/models en_US-lessac-medium en_US-lessac-low; \
    fi

COPY . .

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/storage \
    && chown -R appuser:appuser /app/storage
USER appuser

EXPOSE 8000
# One worker: job state lives in this process (SPEC §5). Render sets $PORT.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
