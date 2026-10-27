# syntax=docker/dockerfile:1.7
ARG PYTHON_IMAGE=python:3.12.15-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.9.5@sha256:f459f6f73a8c4ef5d69f4e6fbbdb8af751d6fa40ec34b39a1ab469acd6e289b7
FROM ${UV_IMAGE} AS uv
FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --extra backend --no-install-project
COPY roma/ roma/
RUN uv sync --locked --no-dev --extra backend

FROM ${PYTHON_IMAGE} AS runtime
ENV PATH="/app/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
WORKDIR /app
RUN groupadd --gid 10001 roma && useradd --uid 10001 --gid 10001 --no-create-home roma \
    && mkdir -p /var/roma && chown 10001:10001 /var/roma
COPY --from=build /app/.venv /app/.venv
COPY roma/ roma/
COPY migrations/ migrations/
COPY alembic.ini ./
COPY docker/ docker/
USER 10001:10001
ENTRYPOINT ["python", "-m", "roma.container_runtime"]
FROM runtime AS api
CMD ["api"]
FROM runtime AS worker
CMD ["worker"]
FROM runtime AS model-contract
CMD ["gpu"]
