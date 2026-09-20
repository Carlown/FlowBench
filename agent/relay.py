"""Shared helpers for the authenticated MQTT server-agent relay."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from urllib.parse import urlsplit


RELAY_SCHEMES = {"mqtt", "mqtts"}
RELAY_TOPIC_PREFIX = "flowbench/agent/v1"
_RELAY_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


def is_relay_url(value: str) -> bool:
    try:
        return urlsplit(str(value or "").strip()).scheme.lower() in RELAY_SCHEMES
    except ValueError:
        return False


def parse_relay_url(value: str) -> dict:
    """Parse ``mqtt(s)://broker:port/relay-id`` into connection settings."""
    raw = str(value or "").strip()
    parsed = urlsplit(raw)
    scheme = parsed.scheme.lower()
    if scheme not in RELAY_SCHEMES or not parsed.hostname:
        raise ValueError("relay_url must be an mqtt(s) URL")
    relay_id = parsed.path.strip("/")
    if not _RELAY_ID_RE.fullmatch(relay_id):
        raise ValueError("relay_url must include a valid relay id")
    try:
        port = parsed.port or (8884 if scheme == "mqtts" else 8000)
    except ValueError as exc:
        raise ValueError("relay_url port is invalid") from exc
    if not 1 <= port <= 65535:
        raise ValueError("relay_url port must be between 1 and 65535")
    return {
        "url": raw.rstrip("/"),
        "broker": parsed.hostname,
        "port": port,
        "relay_id": relay_id,
        "transport": "websockets",
        "tls": scheme == "mqtts",
    }


def relay_topic(relay_id: str, channel: str, agent_id: str = "+") -> str:
    return f"{RELAY_TOPIC_PREFIX}/{relay_id}/{channel}/{agent_id}"


def canonical_message(payload: dict) -> bytes:
    unsigned = {key: value for key, value in dict(payload or {}).items() if key != "signature"}
    return json.dumps(
        unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sign_message(secret: str, payload: dict) -> dict:
    message = dict(payload or {})
    message["signature"] = hmac.new(
        str(secret).encode("utf-8"), canonical_message(message), hashlib.sha256
    ).hexdigest()
    return message


def verify_message(secret: str, payload: dict) -> bool:
    if not isinstance(payload, dict):
        return False
    signature = str(payload.get("signature") or "")
    if len(signature) != 64:
        return False
    expected = hmac.new(
        str(secret).encode("utf-8"), canonical_message(payload), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature, expected)
