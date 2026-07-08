FROM node:20-bookworm-slim AS frontend-builder
WORKDIR /app/frontend

COPY frontend/package*.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    KG_DB_PATH=/app/data/kylinguard.db

WORKDIR /app/backend

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        iproute2 \
        iputils-ping \
        procps \
        sudo \
    && rm -rf /var/lib/apt/lists/*

COPY backend/ /app/backend/
RUN pip install -e /app/backend

COPY --from=frontend-builder /app/frontend/dist /app/frontend/dist
RUN mkdir -p /app/data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).read()"

CMD ["uvicorn", "--factory", "kylinguard.api:create_app", "--host", "0.0.0.0", "--port", "8000"]
