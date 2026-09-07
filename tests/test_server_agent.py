import json
import tempfile
import unittest
from pathlib import Path

from agent.hub import HubState
from server_agent import AgentRuntime, load_config, validate_job


class AgentValidationTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "allowed_targets": ["example.test"],
            "allowed_protocols": ["HTTP", "HTTPS", "TCP"],
            "max_rate": 20,
            "max_duration": 60,
            "max_threads": 4,
            "max_packet_size": 4096,
        }

    def test_valid_job_is_normalized(self):
        job = validate_job({
            "target": "https://example.test/health", "protocol": "HTTPS",
            "rate": 5, "duration": 10, "threads": 2,
            "packet_size": 512, "port": 443,
        }, self.config)
        self.assertEqual(job["target"], "example.test")
        self.assertEqual(job["protocol"], "HTTPS")

    def test_target_and_limits_are_enforced(self):
        with self.assertRaises(ValueError):
            validate_job({"target": "not-allowed.test", "protocol": "HTTP"}, self.config)
        with self.assertRaises(ValueError):
            validate_job({"target": "example.test", "protocol": "UDP", "rate": 1}, self.config)
        with self.assertRaises(ValueError):
            validate_job({"target": "example.test", "protocol": "HTTP", "rate": 21}, self.config)

    def test_provisioned_agent_allows_empty_target_list(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agent.json"
            path.write_text(json.dumps({
                "hub_url": "http://127.0.0.1:8787",
                "agent_id": "a1", "token": "agent-token-1234567890",
                "allowed_targets": [],
            }), encoding="utf-8")
            self.assertEqual(load_config(path)["allowed_targets"], [])

    def test_target_sync_updates_allow_list(self):
        runtime = AgentRuntime(dict(self.config))
        runtime.handle({"type": "sync_targets", "targets": ["https://new.example/path"]})
        self.assertEqual(runtime.config["allowed_targets"], ["new.example"])


class HubStateTests(unittest.TestCase):
    def test_agent_auth_and_command_queue(self):
        state = HubState({
            "controller_token": "controller-token-1234567890",
            "agents": [{"agent_id": "a1", "token": "agent-token-1234567890"}],
        })
        state.register("a1", "agent-token-1234567890", {"hostname": "test"})
        item = state.enqueue("a1", {"type": "stop"})
        self.assertEqual(state.poll("a1", "agent-token-1234567890", 0), item)
        with self.assertRaises(PermissionError):
            state.poll("a1", "wrong-token", 0)

    def test_provision_persists_agent_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hub.json"
            path.write_text(json.dumps({
                "controller_token": "controller-token-1234567890", "agents": []
            }), encoding="utf-8")
            state = HubState(json.loads(path.read_text()), str(path))
            created = state.provision("Linux server")
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["agents"][0]["agent_id"], created["agent_id"])
            self.assertEqual(saved["agents"][0]["token"], created["token"])


if __name__ == "__main__":
    unittest.main()
