"""Runtime settings, read once from environment variables."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    candidate_id: str
    mqtt_host: str
    mqtt_port: int
    heartbeat_seconds: int


def load_settings() -> Settings:
    return Settings(
        database_url=os.environ.get("DATABASE_URL", "postgresql://northbridge:change-me@localhost:5432/northbridge"),
        candidate_id=os.environ.get("CANDIDATE_ID", "CAND-000"),
        mqtt_host=os.environ.get("MQTT_HOST", "152.42.238.142"),
        mqtt_port=int(os.environ.get("MQTT_PORT", "1883")),
        heartbeat_seconds=int(os.environ.get("MQTT_HEARTBEAT_SECONDS", "15")),
    )


settings = load_settings()
