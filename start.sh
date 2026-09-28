#!/usr/bin/env bash
# Start the course assistant. The embedding model runs natively so it can use the GPU
# (Docker on macOS has no GPU access); everything else runs in Docker.
#   ./start.sh                 native embeddings (Apple GPU / NVIDIA GPU / CPU) + Docker
#   EMBEDDINGS_IN_DOCKER=1 ./start.sh   everything in Docker (CPU only, slower)
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -f .env ]]; then
  echo "Missing .env: copy .env.example to .env and set GEMINI_API_KEY." >&2
  exit 1
fi
set -a
# shellcheck disable=SC1091
source .env
set +a
PORT="${EMBEDDING_PORT:-8081}"

if [[ "${EMBEDDINGS_IN_DOCKER:-0}" == "1" ]]; then
  API_EMBEDDING_URL=http://embeddings:8080 docker compose --profile docker-embeddings up --build -d --wait
  echo "Ready: http://localhost:3000 (embeddings in Docker, CPU)"
  exit 0
fi

mkdir -p .run
if curl -sf "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
  echo "Embeddings service already running on port ${PORT}."
else
  if ! command -v uv >/dev/null 2>&1; then
    echo "uv is required to run the embedding model natively: brew install uv" >&2
    exit 1
  fi
  echo "Starting the embedding model natively (first start downloads it, ~1.2 GB)…"
  nohup uv run --quiet --python 3.12 --with-requirements embeddings/requirements.txt \
    uvicorn --app-dir embeddings service:app --host 127.0.0.1 --port "${PORT}" \
    >.run/embeddings.log 2>&1 &
  echo $! >.run/embeddings.pid
fi

printf "Waiting for the embedding model"
for _ in $(seq 1 1800); do
  if health=$(curl -sf "http://127.0.0.1:${PORT}/health" 2>/dev/null) && [[ "${health}" == *'"ready":true'* ]]; then
    echo " ready: ${health}"
    break
  fi
  if [[ -f .run/embeddings.pid ]] && ! kill -0 "$(cat .run/embeddings.pid)" 2>/dev/null; then
    echo; echo "The embedding service stopped; see .run/embeddings.log" >&2
    tail -20 .run/embeddings.log >&2
    exit 1
  fi
  printf "."
  sleep 1
done

docker compose up --build -d --wait
echo "Ready: http://localhost:3000"
