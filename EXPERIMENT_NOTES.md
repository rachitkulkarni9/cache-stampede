# Terminal Screenshot Notes

## Goal

Prove the first fix works: request coalescing should collapse one expiry burst into one DB fetch and one cache rebuild.

## Exact issue to look for

After the hot key expires, many requests for the same `item_id` should still arrive together, but only one of them should rebuild the cache.

That means you should see:

- one `request_coalescing item_id=1 role=leader`
- one `cache_miss item_id=1`
- many `request_coalescing item_id=1 role=waiter`
- one `db_fetch item_id=1`
- one `cache_rebuild item_id=1`

That is the fix: multiple concurrent requests no longer all fall through to Postgres.

## Best terminal output to screenshot

### Screenshot 1: expiry marker followed by coalescing

Watch the app logs and capture the moment where you see:

- `experiment_marker action=expire_hot_key`
- then one `request_coalescing item_id=1 role=leader`
- then many `request_coalescing item_id=1 role=waiter`
- then one `db_fetch item_id=1`
- then one `cache_rebuild item_id=1`

This is the clearest proof that the coalescing fix is working.

### Screenshot 2: hot key is healthy before expiry

Before expiry, capture a short run of:

- `cache_hit item_id=1`

This shows the system is serving from Redis normally until the hot key disappears.

### Screenshot 3: metrics after the run

Call `GET /metrics` and capture the counters, especially:

- `cache_hit_count`
- `cache_miss_count`
- `db_query_count`
- `rebuild_count`

The key point is that `db_query_count` and `rebuild_count` should now rise much more slowly than before. For each expiry burst, they should increase by about one instead of spiking across many concurrent requests.

## One-line article takeaway

When the hot cache entry expires, one request rebuilds it while the other concurrent requests wait for that in-flight work instead of hammering Postgres together.
