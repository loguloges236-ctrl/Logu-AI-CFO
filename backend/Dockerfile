# syntax=docker/dockerfile:1
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --home-dir /nonexistent --shell /usr/sbin/nologin app

COPY requirements.txt ./
RUN pip install -r requirements.txt \
 && rm -rf /root/.cache

# Application code is owned by root and read-only to the runtime user.
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY start.sh ./start.sh
RUN chmod -R a-w /app \
 && mkdir -p /var/lib/logu-ai-cfo/uploads \
 && chown app:app /var/lib/logu-ai-cfo/uploads \
 && chmod 0700 /var/lib/logu-ai-cfo/uploads

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

CMD ["sh", "start.sh"]
