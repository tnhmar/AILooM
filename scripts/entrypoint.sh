#!/bin/sh
set -e
exec uvicorn memory_layer.api.app:create_app \
  --factory \
  --host ${MEMORY_LAYER_SERVER__HOST:-0.0.0.0} \
  --port ${MEMORY_LAYER_SERVER__PORT:-8000} \
  --workers ${MEMORY_LAYER_SERVER__WORKERS:-1}
