#!/usr/bin/env bash
# Backend entrypoint. Roles:
#   api    -> run migrations, then serve the FastAPI app
#   worker -> run the ingestion worker (waits for DB internally)
set -euo pipefail

ROLE="${1:-api}"

case "$ROLE" in
  api)
    echo "[entrypoint] running database migrations..."
    alembic upgrade head
    echo "[entrypoint] starting API..."
    exec uvicorn sentinel.api.app:app --host 0.0.0.0 --port 8000
    ;;
  worker)
    echo "[entrypoint] starting ingestion worker..."
    exec python -m sentinel.worker.main
    ;;
  *)
    # Anything else: run it verbatim (e.g. `docker compose run api bash`).
    exec "$@"
    ;;
esac
