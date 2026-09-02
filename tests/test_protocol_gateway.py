"""Protocol, correlation, and leakage tests for Pattern 9."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
PATTERN = ROOT / "09-aws-interop"
sys.path.insert(0, str(PATTERN))


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


simulator = load_module("pattern9_simulator_test", PATTERN / "simulator.py")
client_module = load_module("pattern9_client_test", PATTERN / "protocol_client.py")
foundry_module = load_module(
    "pattern9_foundry_test",
    PATTERN / "foundry_mcp_agent.py",
)


QUOTE = {
    "capability": "data-analysis",
    "serviceLevel": "priority",
    "units": 250,
    "termMonths": 12,
}


class ProtocolGatewayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(simulator.app)

    def a2a_payload(self, request_id="request-1", message_id="message-1"):
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "message/send",
            "params": {
                "message": {
                    "role": "user",
                    "messageId": message_id,
                    "parts": [{"kind": "data", "data": QUOTE}],
                }
            },
        }

    def test_health_capabilities_and_quote_are_explicitly_simulated(self):
        health = self.client.get("/health").json()
        capabilities = self.client.get("/v1/capabilities").json()
        quote = self.client.post("/v1/quotes", json=QUOTE).json()
        for payload in (health, capabilities, quote):
            self.assertTrue(payload["simulation"])
            self.assertEqual(
                payload["simulatedBackend"],
                simulator.SIMULATED_BACKEND,
            )

    def test_deployed_backend_requires_gateway_authentication(self):
        sentinel = "PATTERN9-BACKEND-GATEWAY-SECRET"
        os.environ["PATTERN9_BACKEND_GATEWAY_KEY"] = sentinel
        self.addCleanup(os.environ.pop, "PATTERN9_BACKEND_GATEWAY_KEY", None)
        protected_requests = [
            self.client.get("/v1/capabilities"),
            self.client.post("/v1/quotes", json=QUOTE),
            self.client.get("/.well-known/agent-card.json"),
            self.client.post("/a2a", json=self.a2a_payload()),
        ]
        self.assertEqual({response.status_code for response in protected_requests}, {401})

        headers = {"X-Pattern9-Backend-Key": sentinel}
        first = self.client.post("/v1/quotes", json=QUOTE, headers=headers).json()
        second = self.client.post("/v1/quotes", json=QUOTE, headers=headers).json()
        self.assertEqual(first, second)
        self.assertNotIn(sentinel, json.dumps(first, sort_keys=True))
        os.environ.pop("PATTERN9_BACKEND_GATEWAY_KEY")
        unkeyed_id = simulator.stable_id("corr", QUOTE)
        self.assertNotEqual(first["correlationId"], unkeyed_id)

    def test_quote_and_a2a_correlation_are_deterministic(self):
        first_quote = self.client.post("/v1/quotes", json=QUOTE).json()
        second_quote = self.client.post("/v1/quotes", json=QUOTE).json()
        self.assertEqual(first_quote, second_quote)

        first = self.client.post("/a2a", json=self.a2a_payload()).json()
        second = self.client.post("/a2a", json=self.a2a_payload()).json()
        self.assertEqual(first, second)
        task = first["result"]
        artifact_quote = task["artifacts"][0]["parts"][0]["data"]
        self.assertEqual(artifact_quote["correlationId"], first_quote["correlationId"])
        self.assertEqual(task["metadata"]["correlationId"], first_quote["correlationId"])

    def test_agent_card_matches_jsonrpc_runtime(self):
        card = self.client.get("/.well-known/agent-card.json").json()
        simulator.validate_agent_card(card, "/a2a")
        self.assertEqual(card["preferredTransport"], "JSONRPC")
        self.assertEqual(card["protocolVersion"], simulator.A2A_PROTOCOL_VERSION)
        self.assertTrue(card["url"].endswith("/a2a"))

    def test_agent_card_runtime_mismatch_is_rejected(self):
        card = simulator.build_agent_card("https://gateway.example")
        card["url"] = "https://gateway.example/not-the-runtime"
        with self.assertRaisesRegex(ValueError, "does not match"):
            simulator.validate_agent_card(card, "/a2a")
        card = simulator.build_agent_card("https://gateway.example")
        card["preferredTransport"] = "SSE"
        with self.assertRaisesRegex(ValueError, "transport"):
            simulator.validate_agent_card(card, "/a2a")

    def test_malformed_jsonrpc_returns_parse_error(self):
        response = self.client.post(
            "/a2a",
            content="{not-json",
            headers={"content-type": "application/json"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["error"]["code"], -32700)
        self.assertIsNone(response.json()["id"])

    def test_invalid_envelope_and_missing_message_id_fail_closed(self):
        invalid = self.client.post(
            "/a2a",
            json={"jsonrpc": "1.0", "id": True, "method": "message/send", "params": {}},
        ).json()
        self.assertEqual(invalid["error"]["code"], -32600)

        missing_id = self.a2a_payload()
        del missing_id["params"]["message"]["messageId"]
        response = self.client.post("/a2a", json=missing_id).json()
        self.assertEqual(response["error"]["code"], -32602)

    def test_wrong_task_id_and_unsupported_method_return_protocol_errors(self):
        missing = self.client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": "get-1",
                "method": "tasks/get",
                "params": {"id": "task-does-not-exist"},
            },
        ).json()
        self.assertEqual(missing["id"], "get-1")
        self.assertEqual(missing["error"]["code"], -32001)

        unsupported = self.client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": "cancel-1",
                "method": "tasks/cancel",
                "params": {"id": "task-1"},
            },
        ).json()
        self.assertEqual(unsupported["error"]["code"], -32601)

    def test_mismatched_response_id_is_rejected_by_client(self):
        with self.assertRaisesRegex(RuntimeError, "ID mismatch"):
            client_module.validate_response_id(
                "request-expected",
                {"jsonrpc": "2.0", "id": "request-other", "result": {}},
            )

    def test_foundry_mcp_content_envelope_yields_structured_quote(self):
        quote = simulator.create_quote(simulator.QuoteRequest(**QUOTE)).model_dump()
        item = SimpleNamespace(
            status="completed",
            output=json.dumps(
                {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(quote),
                        }
                    ]
                }
            ),
        )
        self.assertEqual(foundry_module.quote_from_mcp_call(item), quote)

    def test_foundry_mcp_accepts_structured_content_with_null_text_content(self):
        quote = simulator.create_quote(simulator.QuoteRequest(**QUOTE)).model_dump()
        item = SimpleNamespace(
            status="completed",
            output=json.dumps(
                {
                    "structuredContent": quote,
                    "content": None,
                }
            ),
        )
        self.assertEqual(foundry_module.quote_from_mcp_call(item), quote)

    def test_raw_mcp_skips_non_json_text_before_quote(self):
        quote = simulator.create_quote(simulator.QuoteRequest(**QUOTE)).model_dump()
        result = SimpleNamespace(
            isError=False,
            structuredContent=None,
            content=[
                SimpleNamespace(text="Informational text"),
                SimpleNamespace(text=json.dumps(quote)),
            ],
        )
        self.assertEqual(client_module.extract_mcp_quote(result), quote)

    def test_unsupported_transport_is_rejected(self):
        response = self.client.post(
            "/a2a",
            content="not-jsonrpc",
            headers={"content-type": "text/plain"},
        )
        self.assertEqual(response.status_code, 415)
        self.assertEqual(response.json()["error"]["code"], -32600)

    def test_unknown_fields_and_unsupported_a2a_parts_are_rejected(self):
        quote = {**QUOTE, "secretOverride": "not-allowed"}
        self.assertEqual(self.client.post("/v1/quotes", json=quote).status_code, 422)

        payload = self.a2a_payload()
        payload["params"]["message"]["parts"] = [{"kind": "text", "text": "quote"}]
        response = self.client.post("/a2a", json=payload).json()
        self.assertEqual(response["error"]["code"], -32602)

    def test_secret_like_environment_values_never_leak(self):
        sentinel = "PATTERN9-SECRET-SENTINEL-DO-NOT-LEAK"
        os.environ["PATTERN9_APIM_SUBSCRIPTION_KEY"] = sentinel
        os.environ["PATTERN9_BACKEND_GATEWAY_KEY"] = sentinel
        self.addCleanup(os.environ.pop, "PATTERN9_APIM_SUBSCRIPTION_KEY", None)
        self.addCleanup(os.environ.pop, "PATTERN9_BACKEND_GATEWAY_KEY", None)
        headers = {"X-Pattern9-Backend-Key": sentinel}
        payloads = [
            self.client.get("/health").json(),
            self.client.get("/v1/capabilities", headers=headers).json(),
            self.client.get(
                "/.well-known/agent-card.json",
                headers=headers,
            ).json(),
            self.client.post("/v1/quotes", json=QUOTE, headers=headers).json(),
            self.client.post(
                "/a2a",
                json=self.a2a_payload(),
                headers=headers,
            ).json(),
        ]
        self.assertNotIn(sentinel, json.dumps(payloads, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
