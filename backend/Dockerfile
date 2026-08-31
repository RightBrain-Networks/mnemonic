FROM ghcr.io/astral-sh/uv:0.8.15 AS uv

FROM python:3.13-slim
COPY --from=uv /uv /uvx /bin/
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./
RUN uv sync --frozen --no-dev && \
    groupadd --gid 10001 mnemonic && \
    useradd --uid 10001 --gid mnemonic --no-create-home mnemonic
USER mnemonic
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn mnemonic_api.main:create_app --factory --host 0.0.0.0 --port 8000"]

