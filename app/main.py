import json
import logging
import time
from datetime import datetime
from time import sleep

import redis
from fastapi import FastAPI, HTTPException, Query, Response, status
from psycopg.rows import dict_row

from app.coalescing import InFlightRequest, RequestCoalescer
from app.config import settings
from app.db import build_pool
from app.metrics import MetricsStore

app = FastAPI(title="Cache Stampede Lab", version="0.1.0")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("cache_stampede_lab")


def cache_key(item_id: int) -> str:
    return f"item:{item_id}"


def effective_ttl(item_id: int) -> int:
    if item_id == settings.hot_key_item_id:
        return settings.hot_key_ttl_seconds
    return settings.cache_ttl_seconds


def serialize_item(row: dict) -> dict:
    updated_at = row["updated_at"]
    if isinstance(updated_at, datetime):
        updated_at = updated_at.isoformat()

    return {
        "id": row["id"],
        "slug": row["slug"],
        "value": row["value"],
        "payload": row["payload"],
        "updated_at": updated_at,
    }


@app.on_event("startup")
def startup() -> None:
    app.state.db_pool = build_pool()
    app.state.db_pool.wait()
    app.state.redis = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    app.state.metrics = MetricsStore()
    app.state.coalescer = RequestCoalescer()


@app.on_event("shutdown")
def shutdown() -> None:
    app.state.db_pool.close()
    app.state.redis.close()


@app.get("/healthz")
def healthcheck(response: Response) -> dict:
    db_ok = False
    redis_ok = False

    try:
        with app.state.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        db_ok = True
    except Exception:
        db_ok = False

    try:
        redis_ok = bool(app.state.redis.ping())
    except Exception:
        redis_ok = False

    health_status = "ok" if db_ok and redis_ok else "degraded"
    if health_status != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {"status": health_status, "postgres": db_ok, "redis": redis_ok}


def fetch_item_from_db(item_id: int, simulate_db_ms: int) -> tuple[dict, float]:
    db_started = time.perf_counter()
    if simulate_db_ms:
        sleep(simulate_db_ms / 1000)

    with app.state.db_pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT id, slug, value, payload, updated_at
                FROM items
                WHERE id = %s
                """,
                (item_id,),
            )
            row = cur.fetchone()

    db_ms = round((time.perf_counter() - db_started) * 1000, 2)
    app.state.metrics.increment("db_query_count")
    logger.info("db_fetch item_id=%s db_ms=%s", item_id, db_ms)

    if not row:
        raise HTTPException(status_code=404, detail="Item not found")

    return serialize_item(row), db_ms


def rebuild_cache(item_id: int, item: dict, ttl_seconds: int) -> None:
    app.state.redis.setex(cache_key(item_id), ttl_seconds, json.dumps(item))
    app.state.metrics.increment("rebuild_count")
    logger.info(
        "cache_rebuild item_id=%s ttl_seconds=%s rebuild_count=%s",
        item_id,
        ttl_seconds,
        app.state.metrics.snapshot()["rebuild_count"],
    )


def current_ttl_seconds(item_id: int) -> int | None:
    ttl = app.state.redis.ttl(cache_key(item_id))
    if ttl < 0:
        return None
    return ttl


def request_metrics(latency_ms: float) -> dict:
    app.state.metrics.record_latency(latency_ms)
    return app.state.metrics.snapshot()


def raise_coalesced_error(error: Exception) -> None:
    if isinstance(error, HTTPException):
        raise HTTPException(status_code=error.status_code, detail=error.detail)
    raise RuntimeError("Coalesced rebuild failed") from error


def get_from_coalesced_rebuild(
    item_id: int,
    entry: InFlightRequest,
    response: Response,
    started: float,
    ttl_seconds: int,
) -> dict:
    wait_started = time.perf_counter()
    entry.event.wait()
    wait_ms = round((time.perf_counter() - wait_started) * 1000, 2)

    if entry.error:
        raise_coalesced_error(entry.error)

    response.headers["X-Cache"] = "HIT"
    app.state.metrics.increment("cache_hit_count")
    total_ms = round((time.perf_counter() - started) * 1000, 2)
    metrics = request_metrics(total_ms)
    logger.info(
        "request_coalescing item_id=%s role=waiter wait_ms=%s waiter_count=%s",
        item_id,
        wait_ms,
        entry.waiter_count,
    )
    logger.info(
        "cache_hit item_id=%s request_latency_ms=%s cache_hit_count=%s ttl_remaining_seconds=%s coalesced=True",
        item_id,
        total_ms,
        metrics["cache_hit_count"],
        current_ttl_seconds(item_id),
    )

    return {
        "source": "redis",
        "cache_hit": True,
        "cache_ttl_seconds": ttl_seconds,
        "cache_ttl_remaining_seconds": current_ttl_seconds(item_id),
        "request_latency_ms": total_ms,
        "item": entry.item,
        "metrics": metrics,
    }


@app.post("/admin/cache/warm/{item_id}")
def warm_cache(
    item_id: int,
    simulate_db_ms: int = Query(default=0, ge=0, le=5_000),
) -> dict:
    ttl_seconds = effective_ttl(item_id)
    item, db_ms = fetch_item_from_db(item_id, simulate_db_ms)
    rebuild_cache(item_id, item, ttl_seconds)
    logger.info(
        "experiment_marker action=warm_hot_key item_id=%s ttl_seconds=%s",
        item_id,
        ttl_seconds,
    )

    return {
        "action": "warm",
        "item_id": item_id,
        "cache_key": cache_key(item_id),
        "cache_ttl_seconds": ttl_seconds,
        "db_ms": db_ms,
        "cache_ttl_remaining_seconds": current_ttl_seconds(item_id),
        "metrics": app.state.metrics.snapshot(),
    }


@app.post("/admin/cache/expire/{item_id}")
def expire_cache(item_id: int) -> dict:
    deleted = app.state.redis.delete(cache_key(item_id))
    logger.info(
        "experiment_marker action=expire_hot_key item_id=%s deleted=%s",
        item_id,
        deleted,
    )
    return {
        "action": "expire",
        "item_id": item_id,
        "deleted": bool(deleted),
        "cache_ttl_remaining_seconds": current_ttl_seconds(item_id),
        "metrics": app.state.metrics.snapshot(),
    }


@app.get("/admin/cache/ttl/{item_id}")
def get_cache_ttl(item_id: int) -> dict:
    return {
        "item_id": item_id,
        "cache_key": cache_key(item_id),
        "cache_ttl_remaining_seconds": current_ttl_seconds(item_id),
        "configured_cache_ttl_seconds": effective_ttl(item_id),
    }


@app.post("/admin/cache/wait-for-expiry/{item_id}")
def wait_for_expiry(
    item_id: int,
    timeout_seconds: int = Query(default=30, ge=1, le=300),
    poll_interval_ms: int = Query(default=100, ge=10, le=5_000),
) -> dict:
    deadline = time.perf_counter() + timeout_seconds
    polls = 0

    while time.perf_counter() < deadline:
        polls += 1
        ttl = current_ttl_seconds(item_id)
        if ttl is None:
            logger.info(
                "experiment_marker action=natural_expiry_observed item_id=%s polls=%s",
                item_id,
                polls,
            )
            return {
                "action": "wait_for_expiry",
                "item_id": item_id,
                "expired": True,
                "polls": polls,
                "cache_ttl_remaining_seconds": ttl,
            }
        sleep(poll_interval_ms / 1000)

    ttl = current_ttl_seconds(item_id)
    return {
        "action": "wait_for_expiry",
        "item_id": item_id,
        "expired": False,
        "polls": polls,
        "cache_ttl_remaining_seconds": ttl,
    }


@app.post("/admin/metrics/reset")
def reset_metrics() -> dict:
    app.state.metrics.reset()
    logger.info("metrics_reset")
    return {"action": "metrics_reset", "metrics": app.state.metrics.snapshot()}


@app.get("/metrics")
def get_metrics() -> dict:
    return {
        "metrics": app.state.metrics.snapshot(),
        "hot_key_item_id": settings.hot_key_item_id,
        "hot_key_ttl_seconds": settings.hot_key_ttl_seconds,
        "default_cache_ttl_seconds": settings.cache_ttl_seconds,
    }


@app.get("/items/{item_id}")
def get_item(
    item_id: int,
    response: Response,
    bypass_cache: bool = False,
    simulate_db_ms: int = Query(default=0, ge=0, le=5_000),
) -> dict:
    started = time.perf_counter()
    ttl_seconds = effective_ttl(item_id)
    entry = None

    if not bypass_cache:
        cached = app.state.redis.get(cache_key(item_id))
        if cached:
            response.headers["X-Cache"] = "HIT"
            app.state.metrics.increment("cache_hit_count")
            total_ms = round((time.perf_counter() - started) * 1000, 2)
            metrics = request_metrics(total_ms)
            logger.info(
                "cache_hit item_id=%s request_latency_ms=%s cache_hit_count=%s ttl_remaining_seconds=%s",
                item_id,
                total_ms,
                metrics["cache_hit_count"],
                current_ttl_seconds(item_id),
            )
            return {
                "source": "redis",
                "cache_hit": True,
                "cache_ttl_seconds": ttl_seconds,
                "cache_ttl_remaining_seconds": current_ttl_seconds(item_id),
                "request_latency_ms": total_ms,
                "item": json.loads(cached),
                "metrics": metrics,
            }

        entry, is_leader = app.state.coalescer.acquire(item_id)
        if not is_leader:
            return get_from_coalesced_rebuild(item_id, entry, response, started, ttl_seconds)
        logger.info("request_coalescing item_id=%s role=leader", item_id)

    app.state.metrics.increment("cache_miss_count")
    logger.info(
        "cache_miss item_id=%s cache_miss_count=%s bypass_cache=%s ttl_remaining_seconds=%s",
        item_id,
        app.state.metrics.snapshot()["cache_miss_count"],
        bypass_cache,
        current_ttl_seconds(item_id),
    )
    try:
        item, db_ms = fetch_item_from_db(item_id, simulate_db_ms)
        rebuild_cache(item_id, item, ttl_seconds)
        if entry is not None:
            app.state.coalescer.complete(item_id, entry, item, db_ms)
    except Exception as error:
        if entry is not None:
            app.state.coalescer.fail(item_id, entry, error)
        raise

    response.headers["X-Cache"] = "MISS"
    total_ms = round((time.perf_counter() - started) * 1000, 2)
    metrics = request_metrics(total_ms)
    logger.info(
        "request_complete item_id=%s source=postgres request_latency_ms=%s db_ms=%s",
        item_id,
        total_ms,
        db_ms,
    )

    return {
        "source": "postgres",
        "cache_hit": False,
        "cache_ttl_seconds": ttl_seconds,
        "cache_ttl_remaining_seconds": current_ttl_seconds(item_id),
        "db_ms": db_ms,
        "request_latency_ms": total_ms,
        "item": item,
        "metrics": metrics,
    }
