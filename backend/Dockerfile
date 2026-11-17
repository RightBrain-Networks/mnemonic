FROM ghcr.io/astral-sh/uv:0.13.9@sha256:d3b601f0c7b118bc1753febf7c41cc80981df678445e0254284eecf84db79a7d AS uv

FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928
ARG VERSION=dev
ARG REVISION=unknown
LABEL org.opencontainers.image.source="https://github.com/RightBrain-Networks/mnemonic" \
      org.opencontainers.image.version="$VERSION" \
      org.opencontainers.image.revision="$REVISION" \
      org.opencontainers.image.licenses="AGPL-3.0-only"
COPY --from=uv /uv /uvx /bin/
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    MNEMONIC_EMBEDDING_CACHE=/app/.embedding-cache \
    HF_HUB_DISABLE_TELEMETRY=1 \
    PATH="/app/backend/.venv/bin:$PATH"
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/src ./src
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./
COPY backend/logging.json ./
COPY prompts /app/prompts
COPY scripts/migrate_transcript_copies.py /app/scripts/migrate_transcript_copies.py
COPY scripts/reclaim_transcript_copies.py /app/scripts/reclaim_transcript_copies.py
COPY scripts/audit_transcript_normalization.py /app/scripts/audit_transcript_normalization.py
COPY scripts/recover_transcript_paths.py /app/scripts/recover_transcript_paths.py
COPY scripts/audit_project_activity.py scripts/audit_duplicate_handling.py scripts/audit_code_reviews.py /app/scripts/
COPY tests/fixtures /app/tests/fixtures
RUN uv sync --frozen --no-dev && \
    python -c 'from mnemonic_api.semantic import warm_embedding_model; warm_embedding_model()'
# Compose selects the numeric service identity at runtime. Cached model bytes
# are immutable and readable by arbitrary non-root transcript owners.
RUN groupadd --gid 10001 mnemonic && \
    useradd --uid 10001 --gid 10001 --no-create-home --no-log-init mnemonic && \
    chmod -R a+rX /app/.embedding-cache
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client && \
    rm -rf /var/lib/apt/lists/*
ENV HF_HUB_OFFLINE=1
USER mnemonic
EXPOSE 8000
ENTRYPOINT ["python", "-m", "mnemonic_api.container_runtime"]
CMD ["serve-api"]
