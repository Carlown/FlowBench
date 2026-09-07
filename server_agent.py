"""Headless NetPulse server agent.

The agent is a separate deployment target and does not alter the existing GUI.
It works on Windows Server and Linux because it has no Qt/desktop dependency.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import threading
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import ssl

from agent.transport import compact_json, decode_json, endpoint
from agent.worker import HeadlessStressJob

DEFAULTS = {
    "poll_interval": 2.0, "heartbeat_interval": 10.0, "job_heartbeat_interval": 2.0, "request_timeout": 8.0,
    "max_rate": 100.0, "max_duration": 300, "max_threads": 32,
    "max_packet_size": 64 * 1024, "allowed_protocols": ["HTTP", "HTTPS", "TCP"],
}


class AgentHTTPError(RuntimeError):
    pass


class HubClient:
    def __init__(self, config, config_path=None):
        self.root = config["hub_url"]
        self.agent_id = config["agent_id"]
        self.token = config["token"]
        self.timeout = float(config["request_timeout"])
        self.ssl_context = None
        raw_ca = str(config.get("ca_file") or "").strip()
        if raw_ca:
            ca_path = Path(raw_ca)
            if not ca_path.is_absolute() and config_path is not None:
                ca_path = Path(config_path).resolve().parent / ca_path
            self.ssl_context = ssl.create_default_context(cafile=str(ca_path.resolve()))
        # Self-signed control-plane certificates are accepted only through an
        # explicit opt-in in the generated bundle. Never disable verification
        # for arbitrary targets; test traffic has its own TLS handling.
        if bool(config.get("insecure_control_tls", False)) and self.ssl_context is None:
            self.ssl_context = ssl._create_unverified_context()

    def _request(self, method, path, payload=None, auth=True):
        headers = {"Accept": "application/json"}
        body = None
        if payload is not None:
            body = compact_json(payload)
            headers["Content-Type"] = "application/json"
        if auth:
            headers["Authorization"] = f"Bearer {self.token}"
        req = Request(endpoint(self.root, path), data=body, headers=headers, method=method)
        try:
            with urlopen(req, timeout=self.timeout, context=self.ssl_context) as response:
                data = decode_json(response.read())
                if response.status >= 400:
                    raise AgentHTTPError(data.get("error", f"HTTP {response.status}"))
                return data
        except HTTPError as exc:
            try:
                detail = decode_json(exc.read()).get("error", str(exc))
            except Exception:
                detail = str(exc)
            raise AgentHTTPError(detail) from exc
        except URLError as exc:
            reason = getattr(exc, "reason", exc)
            raise AgentHTTPError(f"{reason} (hub={self.root})") from exc

    def register(self, metadata):
        return self._request("POST", "/v1/agents/register", {
            "agent_id": self.agent_id, "token": self.token, "metadata": metadata,
        }, auth=False)

    def heartbeat(self, event):
        return self._request("POST", f"/v1/agents/{self.agent_id}/heartbeat", event)

    def poll(self, after):
        return self._request("GET", f"/v1/agents/{self.agent_id}/commands?after={int(after)}")

    def event(self, payload):
        return self._request("POST", f"/v1/agents/{self.agent_id}/events", payload)


def _host_from_target(value: str) -> str:
    raw = str(value or "").strip()
    parsed = urlsplit(raw if "://" in raw else f"//{raw}")
    return (parsed.hostname or "").rstrip(".").lower()


def load_config(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    merged = dict(DEFAULTS)
    merged.update(data)
    for key in ("hub_url", "agent_id", "token"):
        if not str(merged.get(key) or "").strip():
            raise ValueError(f"{key} is required")
    if len(str(merged["token"])) < 16:
        raise ValueError("token must be at least 16 characters")
    hub = urlsplit(str(merged["hub_url"]))
    if hub.scheme == "http" and (hub.hostname or "").lower() not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("hub_url must use HTTPS unless it points to localhost")
    # An empty list is valid for a newly provisioned node. The controller
    # sends a signed-by-token sync_targets command before the first job.
    merged["allowed_targets"] = sorted({_host_from_target(x) for x in merged.get("allowed_targets", []) if _host_from_target(x)})
    merged["allowed_protocols"] = [str(x).upper() for x in merged.get("allowed_protocols", DEFAULTS["allowed_protocols"])]
    return merged


def validate_job(raw: dict, config: dict):
    if not isinstance(raw, dict):
        raise ValueError("job config must be an object")
    job = dict(raw)
    target = _host_from_target(job.get("target") or job.get("url"))
    if not target or target not in config["allowed_targets"]:
        raise ValueError(f"target is not in the agent allow-list: {target or '<empty>'}")
    proto = str(job.get("protocol", "HTTP")).upper()
    if proto not in config["allowed_protocols"]:
        raise ValueError(f"protocol is not allowed: {proto}")
    try:
        rate = float(job.get("rate", 1)); duration = int(job.get("duration", 1))
        threads = int(job.get("threads", 1)); packet_size = int(job.get("packet_size", 64))
        port = int(job.get("port", 443 if proto == "HTTPS" else 80)); timeout = int(job.get("timeout", 5000))
    except (TypeError, ValueError) as exc:
        raise ValueError("numeric job fields are invalid") from exc
    if not (0 < rate <= float(config["max_rate"])): raise ValueError("rate exceeds agent limit")
    if not (0 < duration <= int(config["max_duration"])): raise ValueError("duration exceeds agent limit")
    if not (0 < threads <= int(config["max_threads"])): raise ValueError("threads exceed agent limit")
    if not (0 < packet_size <= int(config["max_packet_size"])): raise ValueError("packet_size exceeds agent limit")
    if not (1 <= port <= 65535): raise ValueError("port must be between 1 and 65535")
    if not (100 <= timeout <= 60000): raise ValueError("timeout must be between 100 and 60000 ms")
    if proto in {"HTTP", "HTTPS"}:
        url = str(job.get("url") or "").strip()
        if not url:
            scheme = "https" if proto == "HTTPS" else "http"
            url = f"{scheme}://{target}{'' if port in (80, 443) else ':' + str(port)}"
        parsed = urlsplit(url)
        if parsed.scheme.lower() not in {"http", "https"} or _host_from_target(url) != target:
            raise ValueError("HTTP URL host must match the allow-listed target")
        job["url"] = url
    job.update({"target": target, "protocol": proto, "rate": rate, "duration": duration,
                "threads": threads, "packet_size": packet_size, "port": port, "timeout": timeout})
    return job


class AgentRuntime:
    def __init__(self, config):
        self.config = config
        self.job = None
        self.job_id = None
        self.latest = {"state": "idle"}
        self.lock = threading.RLock()

    def current_event(self):
        with self.lock:
            return dict(self.latest)

    def _set_event(self, value):
        with self.lock:
            self.latest = dict(value)

    def _on_snapshot(self, data):
        event = dict(data or {})
        event.update({"state": "running", "job_id": self.job_id, "ts": time.time()})
        self._set_event(event)

    def _on_report(self, data):
        event = dict(data or {})
        event.update({"state": "completed", "job_id": self.job_id, "ts": time.time()})
        with self.lock:
            self.latest = event
            self.job = None
            self.job_id = None
        print("[agent] job completed")

    def handle(self, command):
        ctype = command.get("type") if isinstance(command, dict) else None
        if ctype == "stop":
            if self.job and self.job.running:
                self.job.stop()
                self._set_event({"state": "stopping", "job_id": self.job_id, "ts": time.time()})
            return
        if ctype == "sync_targets":
            targets = sorted({_host_from_target(x) for x in command.get("targets", []) if _host_from_target(x)})
            if not targets:
                raise ValueError("target sync is empty; authorize a target in the desktop app first")
            self.config["allowed_targets"] = targets
            self._set_event({"state": "ready", "allowed_targets": targets, "ts": time.time()})
            return
        if ctype != "start":
            raise ValueError("unsupported command")
        if self.job and self.job.running:
            raise ValueError("an authorized job is already running")
        job = validate_job(command.get("config") or {}, self.config)
        self.job_id = str(command.get("job_id") or uuid.uuid4().hex)
        self._set_event({"state": "starting", "job_id": self.job_id, "target": job["target"], "protocol": job["protocol"], "ts": time.time()})
        self.job = HeadlessStressJob(job, self._on_snapshot, self._on_report)
        if not self.job.start():
            self.job = None
            self.job_id = None
            raise RuntimeError("job could not start")
        print(f"[agent] job started: {self.job_id}")


def metadata():
    return {"hostname": socket.gethostname(), "platform": platform.platform(), "python": platform.python_version(), "pid": os.getpid()}


def run_agent(config, config_path):
    state_path = Path(config.get("state_file") or (str(config_path) + ".state.json")).expanduser().resolve()
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        cursor = max(0, int(state.get("cursor", 0)))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        cursor = 0
    hub = HubClient(config, config_path)
    runtime = AgentRuntime(config)
    registered = False
    last_heartbeat = 0.0
    while True:
        try:
            now = time.monotonic()
            if not registered:
                hub.register(metadata()); registered = True
                print(f"[agent] online as {config['agent_id']} (hub={config['hub_url']})", flush=True)
            event = runtime.current_event()
            heartbeat_interval = float(config.get("heartbeat_interval", 10))
            if str(event.get("state")) in {"starting", "running", "stopping"}:
                heartbeat_interval = float(config.get("job_heartbeat_interval", 2))
            if now - last_heartbeat >= heartbeat_interval:
                hub.heartbeat({"cursor": cursor, **event})
                last_heartbeat = now
            item = hub.poll(cursor)
            seq = int(item.get("seq", cursor))
            if seq > cursor:
                cursor = seq
                state_path.parent.mkdir(parents=True, exist_ok=True)
                temp = state_path.with_suffix(state_path.suffix + ".tmp")
                temp.write_text(json.dumps({"cursor": cursor}), encoding="utf-8")
                os.replace(temp, state_path)
                if item.get("command"):
                    try:
                        runtime.handle(item["command"])
                    except Exception as exc:
                        runtime._set_event({"state": "error", "error": str(exc), "ts": time.time()})
                        print(f"[agent] command rejected: {exc}")
        except Exception as exc:
            registered = False
            print(f"[agent] control-plane retry: {exc}", flush=True)
        time.sleep(max(0.5, float(config["poll_interval"])))


def main(argv=None):
    parser = argparse.ArgumentParser(description="NetPulse headless server agent")
    parser.add_argument("--config", default="agent.json")
    args = parser.parse_args(argv)
    path = Path(args.config).expanduser().resolve()
    return run_agent(load_config(path), path)


if __name__ == "__main__":
    raise SystemExit(main())
