import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    postgres_dsn: str = os.getenv(
        "POSTGRES_DSN",
        "postgresql://stampede:stampede@localhost:5432/stampede_lab",
    )
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    cache_ttl_seconds: int = int(os.getenv("CACHE_TTL_SECONDS", "5"))
    hot_key_item_id: int = int(os.getenv("HOT_KEY_ITEM_ID", "1"))
    hot_key_ttl_seconds: int = int(os.getenv("HOT_KEY_TTL_SECONDS", "2"))


settings = Settings()
