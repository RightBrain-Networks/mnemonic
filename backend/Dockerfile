FROM ghcr.io/astral-sh/uv:0.8.15 AS uv

FROM python:3.14-slim
COPY --from=uv /uv /uvx /bin/
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    MNEMONIC_EMBEDDING_CACHE=/app/.embedding-cache \
    HF_HUB_DISABLE_TELEMETRY=1 \
    PATH="/app/.venv/bin:$PATH"
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./
RUN uv sync --frozen --no-dev && \
    python -c 'from mnemonic_api.semantic import warm_embedding_model; warm_embedding_model()'
# Match the transcript owner for private (0600) host files. Keep the default
# service identity for installations without shared transcripts.
ARG MNEMONIC_API_UID=10001
ARG MNEMONIC_API_GID=10001
RUN test "$MNEMONIC_API_UID" -gt 0 && test "$MNEMONIC_API_GID" -gt 0 && \
    (getent group "$MNEMONIC_API_GID" >/dev/null || \
     groupadd --gid "$MNEMONIC_API_GID" mnemonic) && \
    useradd --uid "$MNEMONIC_API_UID" --gid "$MNEMONIC_API_GID" \
      --no-create-home --no-log-init mnemonic && \
    chown -R "$MNEMONIC_API_UID:$MNEMONIC_API_GID" /app/.embedding-cache
ENV HF_HUB_OFFLINE=1
USER mnemonic
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn mnemonic_api.main:create_app --factory --host 0.0.0.0 --port 8000 --no-access-log --h11-max-incomplete-event-size 65536"]
