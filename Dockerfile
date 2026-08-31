FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DATABASE_PATH=/data/lss.sqlite3

WORKDIR /app

COPY pyproject.toml ./
COPY lss_report ./lss_report
RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 10001 app && mkdir -p /data && chown app:app /data

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# The Fly volume mounts here; SQLite and its WAL live on it.
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz')"

# Starts as root only long enough to take ownership of the mounted volume, then
# runs the server as the unprivileged app user.
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["lss-web", "--host", "0.0.0.0", "--port", "8000"]
