#!/bin/sh
# Prefer a mounted volume at /data for mutable Clerk org corpora, aggregates, votes.
set -eu
cd /app

if [ -d /data ]; then
  mkdir -p /data/orgs /data/aggregates /data/broker_sim

  if [ ! -L data/orgs ]; then
    rm -rf data/orgs
    ln -s /data/orgs data/orgs
  fi
  if [ ! -L data/aggregates ]; then
    rm -rf data/aggregates
    ln -s /data/aggregates data/aggregates
  fi
  if [ ! -L data/broker_sim ]; then
    # Keep any baked seed_catalog references; only redirect cache dir if present as dir
    if [ -d data/broker_sim ] && [ ! -L data/broker_sim ]; then
      # Move non-link dir aside only if volume is empty of cache
      if [ ! -e /data/broker_sim/.seeded ]; then
        cp -a data/broker_sim/. /data/broker_sim/ 2>/dev/null || true
        touch /data/broker_sim/.seeded
      fi
      rm -rf data/broker_sim
    fi
    ln -s /data/broker_sim data/broker_sim
  fi

  if [ ! -f /data/votes.csv ]; then
    if [ -f data/votes.csv ] && [ ! -L data/votes.csv ]; then
      cp data/votes.csv /data/votes.csv
    else
      : > /data/votes.csv
    fi
  fi
  if [ ! -L data/votes.csv ]; then
    rm -f data/votes.csv
    ln -s /data/votes.csv data/votes.csv
  fi
fi

PORT="${PORT:-8000}"
exec python -m uvicorn app.server:app --host 0.0.0.0 --port "$PORT"
