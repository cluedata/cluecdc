FROM python:3.12-slim
ARG INSTALL_DEV=false
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY apps/api/pyproject.toml ./
COPY apps/api/app ./app
RUN if [ "$INSTALL_DEV" = "true" ]; then pip install --no-cache-dir '.[dev]'; else pip install --no-cache-dir .; fi
COPY apps/api/ ./
RUN useradd --create-home cluecdc && chown -R cluecdc:cluecdc /app
USER cluecdc
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --retries=5 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready')"]
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
