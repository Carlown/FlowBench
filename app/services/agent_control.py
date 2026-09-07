"""Controller API used by the local GUI for server agents."""
from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from pathlib import Path
import ssl

from agent.transport import compact_json, decode_json, endpoint


class AgentControlError(RuntimeError):
    pass


class ControllerClient:
    def __init__(self, hub_url, controller_token, timeout=8, ca_file=""):
        self.hub_url = hub_url
        self.controller_token = controller_token
        self.timeout = float(timeout)
        self.ssl_context = None
        self._unverified_context = None
        ca_file = str(ca_file or "").strip()
        if ca_file and Path(ca_file).is_file():
            self.ssl_context = ssl.create_default_context(cafile=ca_file)

    def call(self, method, path, payload=None):
        body = compact_json(payload) if payload is not None else None
        headers = {"Accept": "application/json", "Authorization": f"Bearer {self.controller_token}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = Request(endpoint(self.hub_url, path), data=body, headers=headers, method=method)
        try:
            context = self.ssl_context
            try:
                with urlopen(req, timeout=self.timeout, context=context) as response:
                    data = decode_json(response.read())
                    if response.status >= 400:
                        raise AgentControlError(data.get("error", f"HTTP {response.status}"))
                    return data
            except (URLError, ssl.SSLError) as exc:
                # One-click bundles use a random self-signed certificate. If the
                # local CA file was moved or an older bundle did not save one,
                # retry the token-authenticated control call once without TLS
                # verification. Regular target traffic is not affected.
                if not self._is_certificate_error(exc):
                    raise
                if self._unverified_context is None:
                    self._unverified_context = ssl._create_unverified_context()
                with urlopen(req, timeout=self.timeout, context=self._unverified_context) as response:
                    data = decode_json(response.read())
                    if response.status >= 400:
                        raise AgentControlError(data.get("error", f"HTTP {response.status}"))
                    return data
        except HTTPError as exc:
            try:
                detail = decode_json(exc.read()).get("error", str(exc))
            except Exception:
                detail = str(exc)
            raise AgentControlError(detail) from exc
        except URLError as exc:
            raise AgentControlError(str(exc.reason)) from exc

    @staticmethod
    def _is_certificate_error(exc):
        reason = getattr(exc, "reason", exc)
        text = str(reason or exc).lower()
        return "certificate_verify_failed" in text or "self-signed certificate" in text

    def list_agents(self):
        return self.call("GET", "/v1/controller/agents").get("agents", [])

    def send(self, agent_id, command):
        return self.call("POST", f"/v1/controller/agents/{agent_id}/commands", {"command": command})

    def provision_agent(self, name, agent_id=""):
        """Create a persistent agent credential on the Hub."""
        payload = {"name": str(name or "").strip()}
        if str(agent_id or "").strip():
            payload["agent_id"] = str(agent_id).strip()
        return self.call("POST", "/v1/controller/agents/provision", payload)
