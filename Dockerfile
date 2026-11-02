# Visora backend image: FastAPI web process or video worker (same image, different command).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CC_DATA_DIR=/data CC_LOG_FORMAT=json CC_DOTENV=0
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg tini \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --upgrade "pip>=26.2" && pip install -r requirements.txt
COPY app ./app
COPY ops ./ops
COPY worker.py ./
RUN useradd --system --uid 10001 --home /app cc && mkdir -p /data && chown cc /data
USER cc
EXPOSE 8000
# Liveness only: the check never touches the database, so an idle Neon compute can scale to zero.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health/live',timeout=4).status==200 else 1)"
ENTRYPOINT ["tini", "--"]
# FORWARDED_ALLOW_IPS (read by uvicorn) must name the reverse proxy / tunnel so client IPs are taken from X-Forwarded-For.
# Keep-alive outlives the proxies' idle connections (Next.js, Caddy, Vercel): with uvicorn's 5 s default, a proxy reusing
# a connection at the moment uvicorn closes it gets ECONNRESET (seen in the V11 load test).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--timeout-keep-alive", "75", "--timeout-graceful-shutdown", "30", "--no-server-header"]
