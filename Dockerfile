# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------------------
# Stage 1: builder
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS builder

RUN pip install uv

WORKDIR /app

# Copy dependency manifest + lock file first so Docker can cache this layer.
# uv.lock MUST be committed to the repository; if it is missing the build
# fails here with a clear error before any source code is copied.
COPY pyproject.toml uv.lock ./

# Install dependencies only (not the project package itself) and cache the
# uv download cache across rebuilds via BuildKit mount cache.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --extra postgres

# Now copy source and install the project itself.
COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --extra postgres

# ---------------------------------------------------------------------------
# Stage 2: runtime
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

WORKDIR /app

COPY --from=builder /app/.venv ./.venv
COPY --from=builder /app/src ./src
COPY scripts/entrypoint.sh ./entrypoint.sh

RUN chmod +x entrypoint.sh

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app/src"

EXPOSE 8000

USER nobody

ENTRYPOINT ["./entrypoint.sh"]
