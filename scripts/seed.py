import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings


def create_table(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY,
                slug TEXT NOT NULL UNIQUE,
                value TEXT NOT NULL,
                payload JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    conn.commit()


def seed_items(conn: psycopg.Connection, count: int, reset: bool) -> None:
    now = datetime.now(timezone.utc)

    with conn.cursor() as cur:
        if reset:
            cur.execute("TRUNCATE TABLE items")

        rows = []
        for item_id in range(1, count + 1):
            rows.append(
                (
                    item_id,
                    f"item-{item_id:06d}",
                    f"value-{item_id}",
                    json.dumps(
                        {
                            "category": f"group-{item_id % 10}",
                            "score": item_id % 100,
                            "active": item_id % 2 == 0,
                        }
                    ),
                    now,
                )
            )

        cur.executemany(
            """
            INSERT INTO items (id, slug, value, payload, updated_at)
            VALUES (%s, %s, %s, %s::jsonb, %s)
            ON CONFLICT (id) DO UPDATE
            SET
                slug = EXCLUDED.slug,
                value = EXCLUDED.value,
                payload = EXCLUDED.payload,
                updated_at = EXCLUDED.updated_at
            """,
            rows,
        )
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed test data into Postgres.")
    parser.add_argument("--count", type=int, default=1000, help="Number of items to seed.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Truncate the table before seeding.",
    )
    args = parser.parse_args()

    with psycopg.connect(settings.postgres_dsn) as conn:
        create_table(conn)
        seed_items(conn, args.count, args.reset)

    print(f"Seeded {args.count} items into Postgres.")


if __name__ == "__main__":
    main()
