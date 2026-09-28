#!/usr/bin/env bash
# Stop the course assistant (data is kept).
set -euo pipefail
cd "$(dirname "$0")"
docker compose --profile docker-embeddings down
if [[ -f .run/embeddings.pid ]]; then
  pid=$(cat .run/embeddings.pid)
  # uv starts uvicorn as a child process: stop the whole group.
  pkill -TERM -P "${pid}" 2>/dev/null || true
  kill "${pid}" 2>/dev/null || true
  rm -f .run/embeddings.pid
  echo "Embedding service stopped."
fi
