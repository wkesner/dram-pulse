# EC Signals — FastAPI feed + static UI (Clerk-ready).
# Does not include data/raw collector caches; mount /data for org votes.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /app

RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates \
  && rm -rf /var/lib/apt/lists/*

COPY requirements-web.txt .
RUN pip install --upgrade pip && pip install -r requirements-web.txt

# App + baked feed corpus (signals/sightings) + config. Mutable paths redirected in entrypoint.
COPY app ./app
COPY src ./src/
COPY dram_signals ./dram_signals
COPY config ./config
COPY data/signals.csv data/sightings.csv data/votes.csv data/seed_catalog.json data/job_boards.json ./data/
COPY data/orgs/.gitkeep data/orgs/.gitkeep
COPY data/aggregates/.gitkeep data/aggregates/.gitkeep
COPY data/broker_sim/.gitkeep data/broker_sim/.gitkeep
COPY data/scoring ./data/scoring
COPY data/partnum_rules.yaml data/part_seed.txt ./data/
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh \
  && mkdir -p data/orgs data/aggregates data/broker_sim

# Optional: empty package marker so `from src.envutil` resolves
RUN touch src/__init__.py

EXPOSE 8000
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/config' % __import__('os').environ.get('PORT','8000'), timeout=3)"

ENTRYPOINT ["/entrypoint.sh"]
