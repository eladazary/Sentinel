# Sentinel backend — API and ingestion worker share one image.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# libgomp1 is the OpenMP runtime LightGBM links against at import time.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Dependencies resolve from pyproject alone, so this layer survives source edits.
# The previous version copied src/ *before* installing, which invalidated this
# layer on every code change and reinstalled the whole tree — torch, transformers,
# lightgbm — turning a one-line fix into a 40-60 minute build. A stub package
# satisfies the build backend without needing the real source here.
COPY pyproject.toml ./
RUN mkdir -p src/sentinel \
    && touch src/sentinel/__init__.py \
    && pip install --upgrade pip \
    && pip install . \
    && rm -rf src

# Only this layer rebuilds when code changes: seconds, not an hour. --no-deps
# because the dependency set is already installed and pinned above.
COPY src ./src
RUN pip install --no-deps --force-reinstall .

# Runtime files needed by the app / migrations.
COPY alembic.ini ./
COPY migrations ./migrations
COPY config ./config
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["entrypoint.sh"]
CMD ["api"]
