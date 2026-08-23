"""Targeted regression tests for the PR review fixes."""
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


eval_gate = load_module("eval_gate_review_test", "07-evaluation-release-gate/run_eval.py")
tracing = load_module("tracing_review_test", "06-observability/enable_tracing.py")
microsoft_iq = load_module("microsoft_iq_review_test", "03-microsoft-iq/microsoft_iq.py")


class FakeResponses:
    def __init__(self, responses):
        self._responses = iter(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return next(self._responses)


class FakeProjectInstrumentor:
    def __init__(self):
        self.options = None

    def instrument(self, **kwargs):
        self.options = kwargs


class RecordingSpan:
    def __init__(self, name):
        self.name = name
        self.attributes = {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def set_attribute(self, name, value):
        self.attributes[name] = value


class RecordingTracer:
    def __init__(self):
        self.spans = []

    def start_as_current_span(self, name):
        span = RecordingSpan(name)
        self.spans.append(span)
        return span


class ReviewFixTests(unittest.TestCase):
    def test_release_fixtures_cannot_contain_static_responses(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bad.jsonl"
            path.write_text(
                json.dumps(
                    {
                        "id": "bad",
                        "query": "q",
                        "context": "c",
                        "ground_truth": "g",
                        "response": "static",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "static response"):
                eval_gate.load_golden_rows(path)

    def test_release_gate_generates_candidate_output_and_demo_is_explicit(self):
        rows = [
            {
                "id": eval_gate.DEMO_FAILURE_ID,
                "query": "Is 90% suitable?",
                "context": "Conservative maximum is 70%.",
                "ground_truth": "No.",
            }
        ]
        client = SimpleNamespace(
            responses=FakeResponses(
                [SimpleNamespace(status="completed", output_text="No, the limit is 70%.")]
            )
        )
        generated = eval_gate.generate_candidate_rows(client, rows, model="candidate-model")
        self.assertEqual(generated[0]["response"], "No, the limit is 70%.")
        self.assertEqual(client.responses.calls[0]["model"], "candidate-model")
        self.assertIn("Authoritative policy context", client.responses.calls[0]["input"])

        demo_client = SimpleNamespace(
            responses=FakeResponses(
                [SimpleNamespace(status="completed", output_text="No, the limit is 70%.")]
            )
        )
        demo = eval_gate.generate_candidate_rows(
            demo_client,
            rows,
            model="candidate-model",
            demo_failure=True,
        )
        self.assertIn("90% equity allocation is suitable", demo[0]["response"])

    def test_release_gate_passes_only_complete_error_free_expected_metrics(self):
        results = [
            SimpleNamespace(testing_criteria=name, passed=5, failed=0, errored=0)
            for name in sorted(eval_gate.EXPECTED_CRITERIA)
        ]
        run = SimpleNamespace(
            status="completed",
            result_counts=SimpleNamespace(total=5, passed=5, failed=0, errored=0),
            per_testing_criteria_results=results,
        )
        self.assertEqual(eval_gate.gate_failures(run, expected_total=5), [])

    def test_release_gate_fails_closed(self):
        valid_results = [
            SimpleNamespace(testing_criteria=name, passed=5, failed=0, errored=0)
            for name in sorted(eval_gate.EXPECTED_CRITERIA)
        ]
        cases = {
            "unsuccessful": SimpleNamespace(
                status="failed",
                result_counts=SimpleNamespace(total=5, passed=5, failed=0, errored=0),
                per_testing_criteria_results=valid_results,
            ),
            "errored-row": SimpleNamespace(
                status="completed",
                result_counts=SimpleNamespace(total=5, passed=4, failed=0, errored=1),
                per_testing_criteria_results=valid_results,
            ),
            "missing-results": SimpleNamespace(
                status="completed",
                result_counts=SimpleNamespace(total=5, passed=5, failed=0, errored=0),
                per_testing_criteria_results=None,
            ),
            "missing-groundedness": SimpleNamespace(
                status="completed",
                result_counts=SimpleNamespace(total=5, passed=5, failed=0, errored=0),
                per_testing_criteria_results=[
                    result
                    for result in valid_results
                    if result.testing_criteria != "groundedness"
                ],
            ),
            "metric-failure": SimpleNamespace(
                status="completed",
                result_counts=SimpleNamespace(total=5, passed=4, failed=1, errored=0),
                per_testing_criteria_results=[
                    SimpleNamespace(
                        testing_criteria=result.testing_criteria,
                        passed=4 if result.testing_criteria == "relevance" else 5,
                        failed=1 if result.testing_criteria == "relevance" else 0,
                        errored=0,
                    )
                    for result in valid_results
                ],
            ),
        }
        for name, run in cases.items():
            with self.subTest(name=name):
                self.assertTrue(eval_gate.gate_failures(run, expected_total=5))

    def test_release_gate_rejects_unaccounted_rows(self):
        results = [
            SimpleNamespace(testing_criteria=name, passed=4, failed=0, errored=0)
            for name in sorted(eval_gate.EXPECTED_CRITERIA)
        ]
        malformed = SimpleNamespace(
            status="completed",
            result_counts=SimpleNamespace(total=5, passed=4, failed=0, errored=0),
            per_testing_criteria_results=results,
        )
        failures = eval_gate.gate_failures(malformed, expected_total=5)
        self.assertTrue(any("result_counts accounts for 4" in failure for failure in failures))
        for name in eval_gate.EXPECTED_CRITERIA:
            self.assertTrue(
                any(f"{name} accounts for 4" in failure for failure in failures)
            )

        truncated = SimpleNamespace(
            status="completed",
            result_counts=SimpleNamespace(total=4, passed=4, failed=0, errored=0),
            per_testing_criteria_results=results,
        )
        self.assertTrue(
            any(
                "expected 5 generated row(s)" in failure
                for failure in eval_gate.gate_failures(truncated, expected_total=5)
            )
        )

    def _instrumentation_mode(self, record_content: bool):
        fake = FakeProjectInstrumentor()
        with patch.dict(
            os.environ,
            {"TRACE_CONTENT_RECORDING": "true" if record_content else "false"},
            clear=False,
        ), patch.object(tracing, "AIProjectInstrumentor", return_value=fake):
            mapped = tracing.configure_trace_content()
            returned = tracing.instrument_foundry(mapped)
            state = {
                "content_env": os.environ[tracing.CONTENT_RECORDING_ENV],
                "experimental_env": os.environ[
                    "AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING"
                ],
            }
        self.assertIs(returned, fake)
        return mapped, state, fake.options

    def test_responses_instrumentation_is_metadata_only_by_default(self):
        mapped, state, options = self._instrumentation_mode(False)
        self.assertFalse(mapped)
        self.assertEqual(state["content_env"], "false")
        self.assertEqual(state["experimental_env"], "true")
        self.assertFalse(options["enable_content_recording"])
        self.assertTrue(options["enable_trace_context_propagation"])
        self.assertFalse(options["enable_baggage_propagation"])

    def test_responses_instrumentation_captures_content_only_on_opt_in(self):
        mapped, state, options = self._instrumentation_mode(True)
        self.assertTrue(mapped)
        self.assertEqual(state["content_env"], "true")
        self.assertTrue(options["enable_content_recording"])
        self.assertTrue(options["enable_trace_context_propagation"])

    def test_invoke_agent_aggregates_multi_response_usage_and_version(self):
        tool_call = SimpleNamespace(
            type="function_call",
            name="get_client_holdings",
            call_id="tool-call-1",
            arguments=json.dumps({"client_id": "C-1290"}),
        )
        responses = FakeResponses(
            [
                SimpleNamespace(
                    id="response-1",
                    output=[tool_call],
                    output_text="",
                    usage=SimpleNamespace(input_tokens=11, output_tokens=3),
                ),
                SimpleNamespace(
                    id="response-2",
                    output=[],
                    output_text="Not compliant.",
                    usage=SimpleNamespace(input_tokens=7, output_tokens=5),
                ),
            ]
        )
        tracer = RecordingTracer()
        answer = tracing.ask(
            SimpleNamespace(responses=responses),
            "rm-assistant-traced",
            7,
            "Is C-1290 compliant?",
            tracer,
        )
        self.assertEqual(answer, "Not compliant.")
        agent_span = next(span for span in tracer.spans if span.name == "invoke_agent")
        self.assertEqual(agent_span.attributes["gen_ai.agent.name"], "rm-assistant-traced")
        self.assertEqual(agent_span.attributes["gen_ai.agent.version"], "7")
        self.assertEqual(agent_span.attributes["gen_ai.usage.input_tokens"], 18)
        self.assertEqual(agent_span.attributes["gen_ai.usage.output_tokens"], 8)

    def test_invoke_agent_handles_missing_usage_without_inventing_tokens(self):
        tracer = RecordingTracer()
        answer = tracing.ask(
            SimpleNamespace(
                responses=FakeResponses(
                    [
                        SimpleNamespace(
                            id="response-1",
                            output=[],
                            output_text="Answer without usage.",
                            usage=None,
                        )
                    ]
                )
            ),
            "rm-assistant-traced",
            "preview",
            "Question",
            tracer,
        )
        self.assertEqual(answer, "Answer without usage.")
        agent_span = next(span for span in tracer.spans if span.name == "invoke_agent")
        self.assertEqual(agent_span.attributes["gen_ai.agent.name"], "rm-assistant-traced")
        self.assertEqual(agent_span.attributes["gen_ai.agent.version"], "preview")
        self.assertNotIn("gen_ai.usage.input_tokens", agent_span.attributes)
        self.assertNotIn("gen_ai.usage.output_tokens", agent_span.attributes)

    def test_search_requires_tool_call_output_and_citations(self):
        citation = SimpleNamespace(type="url_citation", title="Policy", url="https://example.test")
        content = SimpleNamespace(annotations=[citation])
        valid = SimpleNamespace(
            status="completed",
            output_text="Cited answer",
            output=[
                SimpleNamespace(
                    type="azure_ai_search_call",
                    status="completed",
                    call_id="search-call-1",
                ),
                SimpleNamespace(
                    type="azure_ai_search_call_output",
                    status="completed",
                    call_id="search-call-1",
                ),
                SimpleNamespace(type="message", content=[content]),
            ],
        )
        citations = microsoft_iq.validate_search_response(valid)
        self.assertEqual(citations[0]["title"], "Policy")

        with self.assertRaisesRegex(RuntimeError, "no Azure AI Search tool invocation"):
            microsoft_iq.validate_search_response(
                SimpleNamespace(status="completed", output_text="answer", output=[])
            )
        with self.assertRaisesRegex(RuntimeError, "no citation annotations"):
            microsoft_iq.validate_search_response(
                SimpleNamespace(
                    status="completed",
                    output_text="answer",
                    output=[
                        SimpleNamespace(
                            type="azure_ai_search_call",
                            status="completed",
                            call_id="search-call-1",
                        ),
                        SimpleNamespace(
                            type="azure_ai_search_call_output",
                            status="completed",
                            call_id="search-call-1",
                        ),
                    ],
                )
            )

    def test_search_rejects_statusless_or_mismatched_call_items(self):
        citation = SimpleNamespace(type="url_citation", title="Policy", url="https://example.test")
        message = SimpleNamespace(
            type="message",
            content=[SimpleNamespace(annotations=[citation])],
        )
        cases = {
            "statusless-call": [
                SimpleNamespace(type="azure_ai_search_call", status=None, call_id="call-1"),
                SimpleNamespace(
                    type="azure_ai_search_call_output",
                    status="completed",
                    call_id="call-1",
                ),
                message,
            ],
            "statusless-output": [
                SimpleNamespace(
                    type="azure_ai_search_call",
                    status="completed",
                    call_id="call-1",
                ),
                SimpleNamespace(
                    type="azure_ai_search_call_output",
                    status=None,
                    call_id="call-1",
                ),
                message,
            ],
            "mismatched": [
                SimpleNamespace(
                    type="azure_ai_search_call",
                    status="completed",
                    call_id="call-1",
                ),
                SimpleNamespace(
                    type="azure_ai_search_call_output",
                    status="completed",
                    call_id="call-2",
                ),
                message,
            ],
            "unrelated-output": [
                SimpleNamespace(
                    type="azure_ai_search_call",
                    status="completed",
                    call_id="call-1",
                ),
                SimpleNamespace(
                    type="azure_ai_search_call_output",
                    status="completed",
                    call_id="call-1",
                ),
                SimpleNamespace(
                    type="azure_ai_search_call_output",
                    status="completed",
                    call_id="call-2",
                ),
                message,
            ],
        }
        for name, output in cases.items():
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                microsoft_iq.validate_search_response(
                    SimpleNamespace(
                        status="completed",
                        output_text="Cited answer",
                        output=output,
                    )
                )

    def test_web_iq_fails_on_tool_errors_or_missing_sources(self):
        with self.assertRaisesRegex(RuntimeError, "isError=true"):
            microsoft_iq.validate_web_result(SimpleNamespace(isError=True, content=[]))
        with self.assertRaisesRegex(RuntimeError, "no valid webResults"):
            microsoft_iq.validate_web_result(
                SimpleNamespace(
                    isError=False,
                    content=[SimpleNamespace(text=json.dumps({"answer": "no source"}))],
                )
            )

        texts, urls = microsoft_iq.validate_web_result(
            SimpleNamespace(
                isError=False,
                content=[
                    SimpleNamespace(
                        text=json.dumps(
                            {
                                "webResults": [
                                    {
                                        "title": "Source",
                                        "url": "https://example.test/source",
                                        "content": "Substantive grounded result content.",
                                    }
                                ]
                            }
                        )
                    )
                ],
            )
        )
        self.assertEqual(len(texts), 1)
        self.assertEqual(urls, ["https://example.test/source"])

        structured_texts, structured_urls = microsoft_iq.validate_web_result(
            SimpleNamespace(
                isError=False,
                content=[],
                structuredContent={
                    "webResults": [
                        {
                            "title": "Structured source",
                            "url": "https://example.test/structured",
                            "content": "Structured grounded result content.",
                        }
                    ]
                },
            )
        )
        self.assertEqual(len(structured_texts), 1)
        self.assertEqual(structured_urls, ["https://example.test/structured"])

        snippet_texts, snippet_urls = microsoft_iq.validate_web_result(
            SimpleNamespace(
                isError=False,
                content=[],
                structuredContent={
                    "webResults": [
                        {
                            "title": "Snippet source",
                            "url": "https://example.test/snippet",
                            "snippet": "Documented snippet result content.",
                        }
                    ]
                },
            )
        )
        self.assertEqual(len(snippet_texts), 1)
        self.assertEqual(snippet_urls, ["https://example.test/snippet"])

    def test_web_iq_rejects_help_or_error_urls_without_result_sources(self):
        cases = [
            SimpleNamespace(
                isError=False,
                content=[
                    SimpleNamespace(
                        text="Request throttled; docs at https://example.test/help"
                    )
                ],
            ),
            SimpleNamespace(
                isError=False,
                content=[
                    SimpleNamespace(
                        text=json.dumps(
                            {
                                "message": "Request throttled",
                                "help": "https://example.test/help",
                            }
                        )
                    )
                ],
            ),
            SimpleNamespace(
                isError=False,
                content=[
                    SimpleNamespace(
                        text=json.dumps(
                            {
                                "webResults": [
                                    {
                                        "title": "Help",
                                        "url": "https://example.test/help",
                                    }
                                ]
                            }
                        )
                    )
                ],
            ),
        ]
        for result in cases:
            with self.subTest(result=result), self.assertRaisesRegex(
                RuntimeError, "no valid webResults"
            ):
                microsoft_iq.validate_web_result(result)


if __name__ == "__main__":
    unittest.main()
