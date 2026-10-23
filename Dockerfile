# syntax=docker/dockerfile:1.7
ARG PYTHON_IMAGE=python:3.12-slim-bookworm

FROM ${PYTHON_IMAGE} AS wheels
WORKDIR /build
COPY pyproject.toml ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/pip \
    pip wheel --wheel-dir /wheels .

FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
# wheels are bind-mounted, so they never end up in an image layer
RUN --mount=type=bind,from=wheels,source=/wheels,target=/wheels \
    pip install --no-cache-dir --no-index --find-links=/wheels geotrack \
    && adduser --system --group --uid 10100 --no-create-home geotrack
WORKDIR /app
COPY alembic.ini ./
COPY alembic ./alembic
USER geotrack
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=4s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request as r; r.urlopen('http://127.0.0.1:8000/health/ready', timeout=3)"]
CMD ["python", "-m", "geotrack"]
