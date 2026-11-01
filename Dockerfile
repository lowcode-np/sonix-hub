FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOST=0.0.0.0 \
    PORT=8080 \
    HUB_STATE_DIR=/data

WORKDIR /app

COPY hub ./hub

RUN useradd --create-home --uid 10001 sonix \
    && mkdir -p /data \
    && chown -R sonix:sonix /data /app

USER sonix
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=6s --start-period=45s --retries=3 \
    CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT','8080'), timeout=5)"

CMD ["python", "-m", "hub.server"]
