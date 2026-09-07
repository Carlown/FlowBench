"""Operator CLI for the authenticated NetPulse agent hub."""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from agent.transport import compact_json, decode_json, endpoint


def call(config, method, path, payload=None):
    body = compact_json(payload) if payload is not None else None
    req = Request(endpoint(config["hub_url"], path), data=body, method=method, headers={
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config['controller_token']}",
    })
    try:
        with urlopen(req, timeout=float(config.get("request_timeout", 8))) as response:
            return decode_json(response.read())
    except (HTTPError, URLError) as exc:
        detail = str(exc)
        if isinstance(exc, HTTPError):
            try:
                detail = decode_json(exc.read()).get("error", detail)
            except Exception:
                pass
        raise RuntimeError(detail) from exc


def load(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if len(str(data.get("controller_token") or "")) < 16:
        raise ValueError("controller_token must be at least 16 characters")
    return data


def main(argv=None):
    parser = argparse.ArgumentParser(description="NetPulse agent operator CLI")
    parser.add_argument("--config", default="controller.json")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    run = sub.add_parser("run")
    run.add_argument("agent_id")
    run.add_argument("job_json", help="JSON file containing a bounded start config")
    stop = sub.add_parser("stop")
    stop.add_argument("agent_id")
    args = parser.parse_args(argv)
    config = load(args.config)
    if args.command == "list":
        print(json.dumps(call(config, "GET", "/v1/controller/agents"), ensure_ascii=False, indent=2))
    elif args.command == "stop":
        item = call(config, "POST", f"/v1/controller/agents/{args.agent_id}/commands", {"command": {"type": "stop"}})
        print(json.dumps(item, ensure_ascii=False, indent=2))
    else:
        job = json.loads(Path(args.job_json).read_text(encoding="utf-8"))
        command = {"type": "start", "job_id": uuid.uuid4().hex, "config": job}
        item = call(config, "POST", f"/v1/controller/agents/{args.agent_id}/commands", {"command": command})
        print(json.dumps(item, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
