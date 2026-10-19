FROM python:3.12-slim

# Time-zone data for America/Chicago (the slim image has none); the app is otherwise stdlib-only.
RUN pip install --no-cache-dir tzdata==2025.2 \
 && useradd --uid 1000 --create-home app

WORKDIR /app
COPY *.py ./
COPY models/ ./models/
COPY web/ ./web/
COPY deploy/ ./deploy/

# /app/data is the persistent data disk on the server (mounted at run time). It holds the
# snapshots, daily reports and usage counts, and is never replaced by a deploy or rollback.
RUN mkdir -p /app/data/raw && chown -R app:app /app/data
USER app

ENV HOST=0.0.0.0 PORT=8787 PYTHONUNBUFFERED=1 NETWORK_ARCHIVE=off FETCH_WEATHER=on
EXPOSE 8787
HEALTHCHECK --interval=60s --timeout=5s --start-period=120s --retries=3 \
  CMD python -c "import json,urllib.request,sys; sys.exit(0 if json.load(urllib.request.urlopen('http://127.0.0.1:8787/api/health', timeout=4)).get('ok') else 1)"
CMD ["python", "server.py"]
