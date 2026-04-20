# Cache Stampede Lab

Minimal local lab for exploring read-heavy traffic, cache-aside behavior, and cache stampede effects under load.

## Stack

- FastAPI
- Postgres
- Redis
- Docker Compose
- k6

## Folder Structure

```text
.
|-- app/
|   |-- config.py
|   |-- db.py
|   `-- main.py
|-- k6/
|   `-- read-heavy.js
|-- scripts/
|   `-- seed.py
|-- docker-compose.yml
|-- Dockerfile
|-- requirements.txt
`-- README.md
```

## What This Lab Does

- Exposes a small read-heavy API: `GET /items/{item_id}`
- Reads through Redis first using cache-aside logic
- Falls back to Postgres on cache miss
- Returns simple measurement fields such as request latency and cumulative counters
- Lets you simulate a slower backing store with `simulate_db_ms`
- Gives one hot item its own short TTL so it can expire under load
- Adds warmup and forced-expiry controls for repeatable experiments

This is intentionally naive cache-aside behavior so cache stampede effects are easier to observe later.

## Run It On Windows

### 1. Start the stack

From PowerShell in the repo root:

```powershell
docker compose up -d --build postgres redis app
```

Wait until all services are healthy:

```powershell
docker compose ps
```

### 2. Seed test data

```powershell
docker compose exec app python -m scripts.seed --count 1000 --reset
```

### 3. Smoke test the API

First request should miss Redis and hit Postgres:

```powershell
irm "http://localhost:8000/items/1?simulate_db_ms=150"
```

Second request should hit Redis:

```powershell
irm "http://localhost:8000/items/1?simulate_db_ms=150"
```

Warm the hot key explicitly:

```powershell
irm "http://localhost:8000/admin/cache/warm/1?simulate_db_ms=150" -Method Post
```

Inspect the remaining TTL for the hot key:

```powershell
irm "http://localhost:8000/admin/cache/ttl/1"
```

Force the hot key to expire immediately:

```powershell
irm "http://localhost:8000/admin/cache/expire/1" -Method Post
```

Wait until the hot key expires naturally:

```powershell
irm "http://localhost:8000/admin/cache/wait-for-expiry/1?timeout_seconds=30" -Method Post
```

Useful health endpoint:

```powershell
irm "http://localhost:8000/healthz"
```

Useful metrics endpoint:

```powershell
irm "http://localhost:8000/metrics"
```

### 4. Run the load test

This uses the `k6` service from Docker Compose, so you do not need a local k6 install.

Open two PowerShell windows:

- Window 1: watch the app logs
- Window 2: run the test

Window 1:

```powershell
docker compose logs -f app
```

Window 2:

```powershell
irm "http://localhost:8000/admin/metrics/reset" -Method Post
irm "http://localhost:8000/admin/cache/warm/1?simulate_db_ms=250" -Method Post
docker compose --profile tools run --rm `
  -e PROFILE=hot `
  -e HOT_ITEM_ID=1 `
  -e RATE=300 `
  -e DURATION=40s `
  -e SIMULATE_DB_MS=250 `
  -e WARMUP=false `
  -e EXPIRE_MODE=force `
  -e EXPIRE_AT=10s `
  k6
irm "http://localhost:8000/metrics"
```

This is the simplest repeatable stampede demo: warm the hot key once, start sustained traffic, then force expiry while traffic is still running.

Hot-key stampede run using natural expiry:

```powershell
docker compose --profile tools run --rm `
  -e PROFILE=hot `
  -e HOT_ITEM_ID=1 `
  -e RATE=300 `
  -e DURATION=40s `
  -e SIMULATE_DB_MS=250 `
  -e WARMUP=true `
  -e EXPIRE_MODE=natural `
  k6
```

Hot-key stampede run with forced expiry during traffic:

```powershell
docker compose --profile tools run --rm `
  -e PROFILE=hot `
  -e HOT_ITEM_ID=1 `
  -e RATE=300 `
  -e DURATION=40s `
  -e SIMULATE_DB_MS=250 `
  -e WARMUP=true `
  -e EXPIRE_MODE=force `
  -e EXPIRE_AT=10s `
  k6
```

Spread-out key comparison run:

```powershell
docker compose --profile tools run --rm `
  -e PROFILE=spread `
  -e RATE=300 `
  -e DURATION=40s `
  -e SIMULATE_DB_MS=250 `
  -e SPREAD_ITEM_COUNT=1000 `
  k6
```

You can override the load profile:

```powershell
docker compose --profile tools run --rm `
  -e PROFILE=hot `
  -e HOT_ITEM_ID=1 `
  -e RATE=500 `
  -e DURATION=60s `
  -e SIMULATE_DB_MS=400 `
  -e EXPIRE_MODE=force `
  -e EXPIRE_AT=15s `
  k6
```

### 5. Stop the stack

```powershell
docker compose down
```

To also remove the Postgres data volume:

```powershell
docker compose down -v
```

## API Notes

### `GET /items/{item_id}`

Query parameters:

- `simulate_db_ms`: adds artificial latency before the Postgres read
- `bypass_cache`: forces a Postgres read and refreshes Redis

Response fields:

- `source`: `redis` or `postgres`
- `cache_hit`: `true` or `false`
- `cache_ttl_seconds`: current TTL used for Redis entries
- `cache_ttl_remaining_seconds`: remaining TTL in Redis when present
- `db_ms`: included on cache miss
- `request_latency_ms`: total request handling time
- `item`: the seeded payload
- `metrics`: current in-process counters

The response also includes `X-Cache: HIT` or `X-Cache: MISS`.

### Admin endpoints for reproducible tests

- `POST /admin/cache/warm/{item_id}`: populate Redis before a run
- `POST /admin/cache/expire/{item_id}`: delete the cached item immediately while traffic is active
- `GET /admin/cache/ttl/{item_id}`: inspect remaining TTL
- `POST /admin/cache/wait-for-expiry/{item_id}`: wait for natural expiry without stopping traffic
- `POST /admin/metrics/reset`: clear in-process counters before a run
- `GET /metrics`: inspect cumulative counters and TTL config

### Logging

The app logs the key experiment events to container stdout:

- `cache_hit`
- `cache_miss`
- `cache_rebuild`
- `db_fetch`
- `request_complete`

Use PowerShell to watch them during a run:

```powershell
docker compose logs -f app
```

For article screenshots, the most important sequence is:

- `cache_hit item_id=1` before expiry
- `experiment_marker action=expire_hot_key item_id=1`
- a burst of repeated `cache_miss item_id=1`
- a burst of repeated `db_fetch item_id=1`
- a burst of repeated `cache_rebuild item_id=1`

That repeated burst for the same key is the broken behavior.

## Tuning For Stampede Experiments

The default cache TTL is 5 seconds for normal keys. The configured hot key is item `1` with a TTL of `2` seconds so it can expire during an active test. To make stampedes more visible, lower the hot key TTL and increase the simulated DB delay in `docker-compose.yml`:

- `HOT_KEY_TTL_SECONDS=2` or `1`
- `SIMULATE_DB_MS=150` or higher in the k6 run

With many requests targeting the same `HOT_ITEM_ID`, you should see bursts of Postgres reads and cache rebuilds after expiry because there is no request coalescing or cache locking yet.

## Extend Later

This layout is meant to support follow-up experiments such as:

- request coalescing
- Redis locks
- stale-while-revalidate
- cache warming
- query and cache metrics

## Screenshot Guide

See [EXPERIMENT_NOTES.md](/c:/cache-stampede/EXPERIMENT_NOTES.md:1) for the exact terminal output to capture and the issue to call out in your article.
