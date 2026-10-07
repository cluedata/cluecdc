ARG VERSION=development
ARG VCS_REF=unknown

FROM python:3.12-slim AS builder
ARG INSTALL_DEV=false
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY apps/api/pyproject.toml ./
COPY apps/api/app ./app
RUN --mount=type=cache,target=/root/.cache/pip if [ "$INSTALL_DEV" = "true" ]; then \
      pip wheel --wheel-dir /wheels '.[dev]'; \
    else \
      pip wheel --wheel-dir /wheels .; \
    fi

FROM python:3.12-slim AS runtime
ARG VERSION
ARG VCS_REF
LABEL org.opencontainers.image.title="ClueCDC API" \
      org.opencontainers.image.description="ClueCDC CDC control-plane API and worker" \
      org.opencontainers.image.source="https://github.com/cluedata/cluecdc" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.licenses="Apache-2.0"
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
