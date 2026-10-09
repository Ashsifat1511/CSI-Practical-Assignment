"""GET /api/mqtt/status – dashboard read model for MQTT connectivity and challenges."""
from fastapi import APIRouter

from app.config import settings
from app.modules.mqtt.service import get_mqtt_overview

router = APIRouter()


@router.get("/api/mqtt/status")
def mqtt_status():
    return get_mqtt_overview(settings.candidate_id, settings.heartbeat_seconds)
