# Terminal Screenshot Notes

## Goal

Prove the baseline cache-aside implementation stampedes when the hot key expires under load.

## Exact issue to look for

After the hot key expires, many requests for the same `item_id` miss Redis at nearly the same time.

That causes:

- repeated `cache_miss` lines for the same `item_id`
- repeated `db_fetch` lines for the same `item_id`
- repeated `cache_rebuild` lines for the same `item_id`

All of those lines should appear in a tight burst right after the expiry marker.

That burst is the issue: multiple concurrent requests all fall through to Postgres and rebuild the same cache entry independently.

## Best terminal output to screenshot

### Screenshot 1: expiry marker followed by the burst

Watch the app logs and capture the moment where you see:

- `experiment_marker action=expire_hot_key`
- then many `cache_miss item_id=1`
- then many `db_fetch item_id=1`
- then many `cache_rebuild item_id=1`

This is the clearest proof of the stampede.

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

The key point is that `db_query_count` and `rebuild_count` jump during the miss burst for one expired key.

## One-line article takeaway

When the hot cache entry expires, the app does not coordinate concurrent readers, so many requests hit Postgres and rebuild the same value at once.
