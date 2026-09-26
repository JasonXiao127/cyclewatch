# Pin the distro variant for reproducibility. Optionally pin a digest, e.g.:
# FROM python:3.12-slim-bookworm@sha256:<digest>
FROM python:3.12-slim-bookworm

ARG VERSION=dev
LABEL org.opencontainers.image.title="Cyclewatch" \
      org.opencontainers.image.description="Self-hosted iPhone/iPad battery health tracker" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.source="https://github.com/maraudermarauder/cyclewatch"

WORKDIR /app
COPY requirements-prod.txt .
RUN pip install --no-cache-dir -r requirements-prod.txt \
    && useradd -m -u 1000 -U appuser \
    && mkdir -p /app/data/uploads && chown -R appuser:appuser /app
COPY --chown=appuser:appuser app ./app
ENV DATA_DIR=/app/data \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
EXPOSE 8000
USER appuser
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/devices', timeout=4).status == 200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
