FROM python:3.12-slim AS builder
ARG INSTALL_DEV=false
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY apps/api/pyproject.toml ./
COPY apps/api/app ./app
RUN if [ "$INSTALL_DEV" = "true" ]; then \
      pip wheel --no-cache-dir --wheel-dir /wheels '.[dev]'; \
    else \
      pip wheel --no-cache-dir --wheel-dir /wheels .; \
    fi

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-index /wheels/* && rm -rf /wheels
COPY apps/api/ ./
RUN useradd --create-home cluecdc && chown -R cluecdc:cluecdc /app
USER cluecdc
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --retries=5 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready')"]
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
