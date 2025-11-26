#!/usr/bin/env bash
set -euo pipefail

if [[ ${1:-} == "" ]]; then
  echo "Usage: $0 <image> [env_file]" >&2
  echo "Example: $0 rush-policy-api .env" >&2
  exit 1
fi

IMAGE="$1"
ENV_FILE=${2:-}
PORT=${PORT:-8000}
NAME="healthcheck-${RANDOM}"

RUN_ARGS=("-d" "--rm" "--name" "$NAME" "-p" "${PORT}:8000")
if [[ -n "$ENV_FILE" ]]; then
  RUN_ARGS+=("--env-file" "$ENV_FILE")
fi

docker run "${RUN_ARGS[@]}" "$IMAGE" >/dev/null
cleanup() {
  docker logs "$NAME" || true
  docker stop "$NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "Waiting for container $NAME (image $IMAGE) to pass health check..."
for _ in {1..30}; do
  if curl -sf "http://localhost:${PORT}/health" >/dev/null; then
    echo "Health check passed"
    exit 0
  fi
  sleep 2
done

echo "Health check failed" >&2
exit 1
