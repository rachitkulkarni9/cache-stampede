from psycopg_pool import ConnectionPool

from app.config import settings


def build_pool() -> ConnectionPool:
    return ConnectionPool(conninfo=settings.postgres_dsn, min_size=1, max_size=10)

