"""MQTT worker process: outbound client to the examiner simulator.

Run with: python -m app.modules.mqtt.worker
Transport only – every business decision is delegated to handle_mqtt_challenge().
"""
import json
import logging
import secrets
import signal
import threading

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

from app.config import settings
from app.modules.audit.service import register_audit_handlers
from app.modules.mqtt import protocol
from app.modules.mqtt.service import handle_mqtt_challenge, record_worker_status
from app.shared.db import init_pool
from app.shared.timeutil import utcnow

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("mqtt.worker")

QOS = 1


class MqttWorker:
    def __init__(self) -> None:
        self.candidate_id = settings.candidate_id
        self.client_id = f"fse01-{self.candidate_id}-{secrets.token_hex(3)}"
        self.topics = protocol.topics(self.candidate_id)
        self.subscribed = threading.Event()
        self.stopping = threading.Event()

        self.client = mqtt.Client(CallbackAPIVersion.VERSION2, client_id=self.client_id,
                                  protocol=mqtt.MQTTv311, clean_session=True)
        # Broker publishes OFFLINE for us if the connection drops unexpectedly.
        self.client.will_set(self.topics["status"], self._status_payload("OFFLINE"), qos=QOS, retain=False)
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)  # exponential backoff
        self.client.on_connect = self.on_connect
        self.client.on_subscribe = self.on_subscribe
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message

    def _status_payload(self, status: str) -> str:
        return json.dumps(protocol.build_status(self.candidate_id, self.client_id, status, utcnow()))

    def publish_status(self, status: str) -> None:
        self.client.publish(self.topics["status"], self._status_payload(status), qos=QOS, retain=False)

    # --- callbacks -------------------------------------------------------------------------------
    def on_connect(self, client, _userdata, _flags, reason_code, _props) -> None:
        if reason_code.is_failure:
            log.warning("connect refused: %s", reason_code)
            record_worker_status(connection_state="CONNECT_FAILED", last_error=f"connect refused: {reason_code}",
                                 last_error_at=utcnow())
            return
        log.info("connected to %s:%s as %s", settings.mqtt_host, settings.mqtt_port, self.client_id)
        record_worker_status(connection_state="CONNECTED", last_connected_at=utcnow(),
                             candidate_id=self.candidate_id, client_id=self.client_id)
        client.subscribe(self.topics["challenge"], qos=QOS)  # (re)subscribe on every connect

    def on_subscribe(self, _client, _userdata, _mid, reason_codes, _props) -> None:
        if any(rc.is_failure for rc in reason_codes):
            record_worker_status(last_error="subscription refused", last_error_at=utcnow())
            return
        log.info("subscribed to %s", self.topics["challenge"])
        self.subscribed.set()
        self.publish_status("ONLINE")
        record_worker_status(connection_state="ONLINE", last_heartbeat_at=utcnow())

    def on_disconnect(self, _client, _userdata, _flags, reason_code, _props) -> None:
        self.subscribed.clear()
        if self.stopping.is_set():
            record_worker_status(connection_state="OFFLINE")
            return
        log.warning("disconnected (%s); paho will reconnect with backoff", reason_code)
        record_worker_status(connection_state="RECONNECTING", last_error=f"disconnected: {reason_code}",
                             last_error_at=utcnow())

    def on_message(self, client, _userdata, message) -> None:
        received_at = utcnow()
        log.info("challenge received on %s", message.topic)
        response = handle_mqtt_challenge(message.payload, self.candidate_id, received_at)
        client.publish(self.topics["response"], json.dumps(response), qos=QOS, retain=False)
        error = response.get("error") or {}
        log.info("response %s published for %s", response["status"], response.get("challenge_id"))
        fields = dict(last_challenge_id=response.get("challenge_id"), last_challenge_at=received_at,
                      last_response_status=response["status"] + (f" / {error['code']}" if error else ""))
        if error:
            fields.update(last_error=f"{error['code']}: {error['message']}", last_error_at=utcnow())
        record_worker_status(**fields)

    # --- lifecycle -------------------------------------------------------------------------------
    def heartbeat_loop(self) -> None:
        while not self.stopping.wait(settings.heartbeat_seconds):
            if self.subscribed.is_set():
                self.publish_status("HEARTBEAT")
                record_worker_status(connection_state="ONLINE", last_heartbeat_at=utcnow())

    def stop(self, *_args) -> None:
        log.info("shutting down")
        self.stopping.set()
        if self.subscribed.is_set():
            # A clean disconnect suppresses the last will, so announce OFFLINE explicitly.
            # Packets are sent in queue order, so OFFLINE leaves before the DISCONNECT.
            self.publish_status("OFFLINE")
        self.client.disconnect()

    def run(self) -> None:
        record_worker_status(connection_state="CONNECTING", candidate_id=self.candidate_id, client_id=self.client_id)
        threading.Thread(target=self.heartbeat_loop, daemon=True).start()
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        self.client.connect_async(settings.mqtt_host, settings.mqtt_port, keepalive=30)
        self.client.loop_forever(retry_first_connection=True)


def main() -> None:
    init_pool()
    register_audit_handlers()
    MqttWorker().run()


if __name__ == "__main__":
    main()
