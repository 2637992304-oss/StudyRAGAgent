ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.13.1
ARG PYTHON_IMAGE=python:3.13-slim
FROM ${UV_IMAGE} AS uv
FROM ${PYTHON_IMAGE} AS runtime
COPY --from=uv /uv /usr/local/bin/uv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_LINK_MODE=copy \
    PATH="/app/backend/.venv/bin:$PATH"
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --extra ocr --no-install-project
COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./
COPY backend/scripts ./scripts
RUN uv sync --frozen --no-dev --extra ocr --no-editable \
    && mkdir -p /data/storage /data/models
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]

FROM runtime AS test
COPY backend/tests ./tests
COPY infra/ci /ci
RUN uv sync --frozen --extra dev --extra ocr --no-editable
CMD ["pytest", "-q"]
