FROM node:22-bookworm-slim AS ui
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-chi-sim && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY backend/ backend/
COPY --from=ui /build/dist frontend/dist
RUN useradd --uid 10001 --create-home parts && mkdir -p /store/data /store/backups && chown -R parts:parts /store /app
USER parts
ENV PARTS_DATA_DIR=/store/data PYTHONUNBUFFERED=1
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=3s CMD ["/app/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=2)"]
CMD ["/app/.venv/bin/uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8765", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1"]
