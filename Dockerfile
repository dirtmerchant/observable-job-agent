FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

# Install dependencies first (layer cache)
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy application source and data
COPY src/ src/
COPY data/cached_jobs.json data/cached_jobs.json

# Install the project itself
RUN uv sync --frozen --no-dev \
 && mkdir -p /app/data \
 && chown -R 1000:1000 /app/.venv /app/data

# Non-root user
USER 1000

ENV GRADIO_SERVER_NAME=0.0.0.0
ENV UV_CACHE_DIR=/tmp/.uv-cache
ENV CACHE_PATH=/app/data/cached_jobs.json
ENV SCOUT_DB_PATH=/app/data/scout.db
EXPOSE 7860

ENTRYPOINT ["uv", "run", "python", "-m", "job_scout.app"]
