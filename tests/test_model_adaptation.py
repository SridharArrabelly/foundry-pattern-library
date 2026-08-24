"""Offline validation and release-gate tests for Pattern 14."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "14-model-adaptation"))


def load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


adapt = load_module("model_adaptation_test", "14-model-adaptation/adapt_model.py")


class FakeResponses:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        output = next(self.outputs)
        return SimpleNamespace(
            status="completed",
            output_text=json.dumps(output),
            usage=SimpleNamespace(input_tokens=20, output_tokens=10),
        )


class ContextStub(SimpleNamespace):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class ModelAdaptationTests(unittest.TestCase):
    def test_checked_in_datasets_are_valid_separate_and_pii_free(self):
        report = adapt.validate_datasets()
        self.assertGreaterEqual(report["counts"]["train"], 10)
        self.assertTrue(report["held_out_separate"])
        self.assertEqual(report["pii_scan"], "passed")
        self.assertEqual(set(report["distribution"]["test"]), adapt.CATEGORIES)

    def test_dataset_validator_rejects_held_out_leakage(self):
        with tempfile.TemporaryDirectory() as temp:
            test_path = Path(temp) / "test.jsonl"
            first_train = adapt.read_jsonl(adapt.TRAIN_PATH)[0]
            duplicate = first_train["messages"][1]["content"]
            test_path.write_text(
                json.dumps(
                    {
                        "id": "leak",
                        "input": duplicate,
                        "expected_category": "AX7",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with patch.object(adapt, "TEST_PATH", test_path), self.assertRaisesRegex(
                ValueError, "held-out leakage"
            ):
                adapt.validate_datasets()

    def test_output_validator_rejects_extra_fields_and_invented_categories(self):
        valid = adapt.validate_output_text(
            '{"category":"UNSUPPORTED","rationale":"No supported route applies."}'
        )
        self.assertEqual(valid["category"], "UNSUPPORTED")
        with self.assertRaisesRegex(ValueError, "exactly"):
            adapt.validate_output_text(
                '{"category":"UNSUPPORTED","rationale":"No.","extra":true}'
            )
        with self.assertRaisesRegex(ValueError, "unsupported"):
            adapt.validate_output_text(
                '{"category":"new_category","rationale":"Invented."}'
            )

    def test_identical_evaluation_path_scores_schema_accuracy_tokens_and_latency(self):
        outputs = [
            {
                "category": row["expected_category"],
                "rationale": "The request matches this supported category.",
            }
            for row in adapt.read_jsonl(adapt.TEST_PATH)
        ]
        responses = FakeResponses(outputs)
        result = adapt.evaluate(
            SimpleNamespace(responses=responses),
            deployment="base-deployment",
            label="base",
        )
        self.assertEqual(result["metrics"]["schema_validity"], 1.0)
        self.assertEqual(result["metrics"]["classification_accuracy"], 1.0)
        self.assertEqual(result["metrics"]["task_adherence"], 1.0)
        self.assertEqual(
            result["metrics"]["input_tokens_total"],
            20 * len(outputs),
        )
        self.assertTrue(
            all(call["text"]["format"]["strict"] for call in responses.calls)
        )

    def evaluation(self, *, accuracy, adherence=1.0, latency=100.0, tokens=100):
        by_category = {
            category: {"count": 3, "accuracy": accuracy}
            for category in sorted(adapt.CATEGORIES)
        }
        return {
            "evaluation_id": f"eval-{accuracy}-{latency}",
            "test_sha256": "same",
            "metrics": {
                "schema_validity": 1.0,
                "classification_accuracy": accuracy,
                "task_adherence": adherence,
                "input_tokens_total": 200,
                "output_tokens_total": tokens,
                "latency_mean_ms": latency,
                "latency_p95_ms": latency,
                "by_category": by_category,
            },
        }

    def test_release_gate_passes_measured_gain_and_blocks_regressions(self):
        thresholds = {
            "minimum_tuned_accuracy": 0.9,
            "minimum_accuracy_gain": 0.05,
            "minimum_schema_validity": 1.0,
            "minimum_task_adherence": 1.0,
            "minimum_task_adherence_gain": 0.0,
            "maximum_latency_regression_ratio": 0.25,
            "maximum_output_token_regression_ratio": 0.1,
            "block_per_category_accuracy_regression": True,
        }
        passed = adapt.compare_evaluations(
            self.evaluation(accuracy=0.75),
            self.evaluation(accuracy=1.0, latency=110, tokens=105),
            thresholds,
        )
        self.assertTrue(passed["passed"], passed["failures"])

        regressed = self.evaluation(accuracy=1.0)
        regressed["metrics"]["by_category"]["UNSUPPORTED"]["accuracy"] = 0.5
        failed = adapt.compare_evaluations(
            self.evaluation(accuracy=0.75),
            regressed,
            thresholds,
        )
        self.assertFalse(failed["passed"])
        self.assertTrue(
            any("UNSUPPORTED accuracy regressed" in item for item in failed["failures"])
        )

    def test_submission_requires_matching_baseline_before_upload(self):
        report = adapt.validate_datasets()
        with tempfile.TemporaryDirectory() as temp:
            missing = Path(temp) / "missing.json"
            with self.assertRaisesRegex(RuntimeError, "baseline evaluation is required"):
                adapt.require_baseline(missing, report)
            wrong = Path(temp) / "wrong.json"
            wrong.write_text(
                json.dumps(
                    {
                        "label": "base",
                        "test_sha256": "wrong",
                        "evaluation_id": "eval-1",
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "does not match"):
                adapt.require_baseline(wrong, report)

    def test_region_price_and_effective_permission_checks_fail_closed(self):
        config = adapt.Config(
            project_endpoint="https://example.services.ai.azure.com/api/projects/p",
            resource_id=(
                "/subscriptions/sub/resourceGroups/rg/providers/"
                "Microsoft.CognitiveServices/accounts/account"
            ),
            region="westeurope",
            base_model="gpt-4.1-mini",
            base_model_version="2025-04-14",
            base_deployment="gpt-4.1-mini",
            tuned_deployment="temporary",
            training_type="Standard",
            deployment_sku="DeveloperTier",
            n_epochs=3,
            batch_size=1,
            learning_rate_multiplier=0.1,
            seed=42,
            training_price_per_million_usd=None,
            arm_account_api_version="2026-07-01",
            arm_deployment_api_version="2026-07-01",
        )
        with self.assertRaisesRegex(ValueError, "supported only"):
            config.validate()
        self.assertTrue(
            adapt.action_allowed(
                [{"actions": ["Microsoft.CognitiveServices/*"], "notActions": []}],
                "Microsoft.CognitiveServices/accounts/deployments/write",
            )
        )
        self.assertFalse(
            adapt.action_allowed(
                [
                    {
                        "actions": ["Microsoft.CognitiveServices/*"],
                        "notActions": [
                            "Microsoft.CognitiveServices/accounts/deployments/write"
                        ],
                    }
                ],
                "Microsoft.CognitiveServices/accounts/deployments/write",
            )
        )

    def test_resume_provenance_rejects_unrelated_or_modified_jobs(self):
        report = adapt.validate_datasets()
        config = adapt.Config(
            project_endpoint="https://example.services.ai.azure.com/api/projects/default",
            resource_id=(
                "/subscriptions/sub/resourceGroups/rg/providers/"
                "Microsoft.CognitiveServices/accounts/account"
            ),
            region="swedencentral",
            base_model="gpt-4.1-mini",
            base_model_version="2025-04-14",
            base_deployment="gpt-4.1-mini",
            tuned_deployment="temporary",
            training_type="GlobalStandard",
            deployment_sku="DeveloperTier",
            n_epochs=3,
            batch_size=1,
            learning_rate_multiplier=0.1,
            seed=42,
            training_price_per_million_usd=5.0,
            arm_account_api_version="2026-07-01",
            arm_deployment_api_version="2026-07-01",
        )
        baseline = {
            "evaluation_id": "eval-base",
            "label": "base",
            "deployment": config.base_deployment,
            "test_sha256": report["hashes"]["test_sha256"],
        }
        hyperparameters = {
            "n_epochs": 3,
            "batch_size": 1,
            "learning_rate_multiplier": 0.1,
        }
        submission = {
            "job_id": "job-1",
            "base_model": config.base_model,
            "base_model_version": config.base_model_version,
            "base_deployment": config.base_deployment,
            "training_type": config.training_type,
            "hyperparameters": hyperparameters,
            "training_file_id": "file-train",
            "validation_file_id": "file-validation",
            "dataset": report,
            "baseline_evaluation_id": baseline["evaluation_id"],
            "evaluation_protocol_sha256": report["evaluation_protocol_sha256"],
            "seed": config.seed,
        }
        job_data = {
            "id": "job-1",
            "model": "gpt-4.1-mini-2025-04-14",
            "metadata": {
                "base_model": "gpt-4.1-mini",
                "model_version": "2025-04-14",
            },
            "trainingType": "globalStandard",
            "training_file": "file-train",
            "validation_file": "file-validation",
            "seed": 42,
            "hyperparameters": hyperparameters,
        }
        job = SimpleNamespace(model_dump=lambda mode: job_data)
        adapt.validate_resume_provenance(
            config,
            report,
            submission,
            baseline,
            job,
        )
        forged = {**submission, "job_id": "job-other"}
        with self.assertRaisesRegex(RuntimeError, "job ID"):
            adapt.validate_resume_provenance(
                config,
                report,
                forged,
                baseline,
                job,
            )
        changed_protocol = {
            **submission,
            "evaluation_protocol_sha256": "changed",
        }
        with self.assertRaisesRegex(RuntimeError, "protocol hash"):
            adapt.validate_resume_provenance(
                config,
                report,
                changed_protocol,
                baseline,
                job,
            )

    def test_resume_invalid_provenance_owns_nothing_but_valid_resume_cleans_files(self):
        report = adapt.validate_datasets()
        config = adapt.Config(
            project_endpoint="https://example.services.ai.azure.com/api/projects/default",
            resource_id=(
                "/subscriptions/sub/resourceGroups/rg/providers/"
                "Microsoft.CognitiveServices/accounts/account"
            ),
            region="swedencentral",
            base_model="gpt-4.1-mini",
            base_model_version="2025-04-14",
            base_deployment="gpt-4.1-mini",
            tuned_deployment="temporary",
            training_type="GlobalStandard",
            deployment_sku="DeveloperTier",
            n_epochs=3,
            batch_size=1,
            learning_rate_multiplier=0.1,
            seed=42,
            training_price_per_million_usd=5.0,
            arm_account_api_version="2026-07-01",
            arm_deployment_api_version="2026-07-01",
        )
        baseline = {
            "evaluation_id": "eval-base",
            "label": "base",
            "deployment": config.base_deployment,
            "test_sha256": report["hashes"]["test_sha256"],
        }
        hyperparameters = {
            "n_epochs": 3,
            "batch_size": 1,
            "learning_rate_multiplier": 0.1,
        }
        submission = {
            "job_id": "job-1",
            "base_model": config.base_model,
            "base_model_version": config.base_model_version,
            "base_deployment": config.base_deployment,
            "training_type": config.training_type,
            "hyperparameters": hyperparameters,
            "training_file_id": "file-train",
            "validation_file_id": "file-validation",
            "dataset": report,
            "baseline_evaluation_id": baseline["evaluation_id"],
            "evaluation_protocol_sha256": report["evaluation_protocol_sha256"],
            "seed": config.seed,
        }

        def job(job_id):
            data = {
                "id": job_id,
                "model": "gpt-4.1-mini-2025-04-14",
                "metadata": {
                    "base_model": "gpt-4.1-mini",
                    "model_version": "2025-04-14",
                },
                "trainingType": "globalStandard",
                "training_file": "file-train",
                "validation_file": "file-validation",
                "seed": 42,
                "hyperparameters": hyperparameters,
            }
            return SimpleNamespace(
                id=job_id,
                status="succeeded",
                fine_tuned_model="ft:model",
                trained_tokens=100,
                model_dump=lambda mode: data,
            )

        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            submission_path = temp / "submission.json"
            baseline_path = temp / "baseline.json"
            output_path = temp / "resume.json"
            adapt.write_json(submission_path, submission)
            adapt.write_json(baseline_path, baseline)

            jobs = SimpleNamespace(
                retrieve=Mock(return_value=job("unrelated-job")),
                cancel=Mock(),
            )
            files = SimpleNamespace(delete=Mock())
            client = ContextStub(
                fine_tuning=SimpleNamespace(jobs=jobs),
                files=files,
                models=SimpleNamespace(delete=Mock()),
            )
            clients = (
                ContextStub(),
                ContextStub(),
                client,
            )
            with patch.object(adapt, "project_clients", return_value=clients), patch.object(
                adapt,
                "online_preflight",
                return_value={},
            ), self.assertRaisesRegex(RuntimeError, "resume failed"):
                adapt.resume_evaluation(
                    config,
                    submission_record_path=submission_path,
                    baseline_path=baseline_path,
                    output_path=output_path,
                    confirm_evaluation_cost=True,
                )
            jobs.cancel.assert_not_called()
            files.delete.assert_not_called()

            jobs.retrieve = Mock(return_value=job("job-1"))
            files.delete.reset_mock()
            client.models.delete.reset_mock()
            with patch.object(adapt, "project_clients", return_value=clients), patch.object(
                adapt,
                "online_preflight",
                return_value={},
            ), patch.object(
                adapt,
                "deploy_for_evaluation",
                side_effect=RuntimeError("deployment failed"),
            ), patch.object(
                adapt,
                "arm_request",
                return_value=SimpleNamespace(status_code=404),
            ), self.assertRaisesRegex(RuntimeError, "resume failed"):
                adapt.resume_evaluation(
                    config,
                    submission_record_path=submission_path,
                    baseline_path=baseline_path,
                    output_path=output_path,
                    confirm_evaluation_cost=True,
                )
            self.assertEqual(
                {call.args[0] for call in files.delete.call_args_list},
                {"file-train", "file-validation"},
            )

    def test_cleanup_timeout_still_attempts_model_and_file_cleanup(self):
        jobs = SimpleNamespace(
            retrieve=Mock(return_value=SimpleNamespace(status="succeeded")),
            cancel=Mock(),
        )
        files = SimpleNamespace(delete=Mock())
        models = SimpleNamespace(delete=Mock())
        client = SimpleNamespace(
            fine_tuning=SimpleNamespace(jobs=jobs),
            files=files,
            models=models,
        )
        config = adapt.Config(
            project_endpoint="https://example.services.ai.azure.com/api/projects/default",
            resource_id=(
                "/subscriptions/sub/resourceGroups/rg/providers/"
                "Microsoft.CognitiveServices/accounts/account"
            ),
            region="swedencentral",
            base_model="gpt-4.1-mini",
            base_model_version="2025-04-14",
            base_deployment="gpt-4.1-mini",
            tuned_deployment="temporary",
            training_type="GlobalStandard",
            deployment_sku="DeveloperTier",
            n_epochs=3,
            batch_size=1,
            learning_rate_multiplier=0.1,
            seed=42,
            training_price_per_million_usd=5.0,
            arm_account_api_version="2026-07-01",
            arm_deployment_api_version="2026-07-01",
        )
        record = {
            "job_id": "job-1",
            "owned_deployment": {
                "deployment_name": "temporary-owned123",
                "fine_tuned_model": "ft:model",
                "sku": "DeveloperTier",
            },
            "fine_tuned_model": "ft:model",
            "training_file_id": "file-train",
            "validation_file_id": "file-validation",
        }
        with patch.object(
            adapt,
            "arm_request",
            side_effect=adapt.requests.Timeout("timeout"),
        ), self.assertRaisesRegex(RuntimeError, "cleanup incomplete"):
            adapt.cleanup(SimpleNamespace(), client, config, record)
        models.delete.assert_called_once_with("ft:model")
        self.assertEqual(files.delete.call_count, 2)
        self.assertTrue(record["cleanup"]["errors"])

    def test_cleanup_cancels_nonterminal_job_before_file_deletion(self):
        jobs = SimpleNamespace(
            retrieve=Mock(
                side_effect=[
                    SimpleNamespace(status="running"),
                    SimpleNamespace(status="cancelled"),
                ]
            ),
            cancel=Mock(),
        )
        files = SimpleNamespace(delete=Mock())
        client = SimpleNamespace(
            fine_tuning=SimpleNamespace(jobs=jobs),
            files=files,
            models=SimpleNamespace(delete=Mock()),
        )
        config = adapt.Config(
            project_endpoint="https://example.services.ai.azure.com/api/projects/default",
            resource_id=(
                "/subscriptions/sub/resourceGroups/rg/providers/"
                "Microsoft.CognitiveServices/accounts/account"
            ),
            region="swedencentral",
            base_model="gpt-4.1-mini",
            base_model_version="2025-04-14",
            base_deployment="gpt-4.1-mini",
            tuned_deployment="temporary",
            training_type="GlobalStandard",
            deployment_sku="DeveloperTier",
            n_epochs=3,
            batch_size=1,
            learning_rate_multiplier=0.1,
            seed=42,
            training_price_per_million_usd=5.0,
            arm_account_api_version="2026-07-01",
            arm_deployment_api_version="2026-07-01",
        )
        record = {
            "job_id": "job-running",
            "training_file_id": "file-train",
        }
        adapt.cleanup(SimpleNamespace(), client, config, record)
        jobs.cancel.assert_called_once_with("job-running")
        files.delete.assert_called_once_with("file-train")
        self.assertEqual(
            record["cleanup"]["job_status_after_cleanup"],
            "cancelled",
        )

    def test_temporary_deployment_ownership_prevents_foreign_delete(self):
        config = adapt.Config(
            project_endpoint="https://example.services.ai.azure.com/api/projects/default",
            resource_id=(
                "/subscriptions/sub/resourceGroups/rg/providers/"
                "Microsoft.CognitiveServices/accounts/account"
            ),
            region="swedencentral",
            base_model="gpt-4.1-mini",
            base_model_version="2025-04-14",
            base_deployment="gpt-4.1-mini",
            tuned_deployment="temporary-eval",
            training_type="GlobalStandard",
            deployment_sku="DeveloperTier",
            n_epochs=3,
            batch_size=1,
            learning_rate_multiplier=0.1,
            seed=42,
            training_price_per_million_usd=5.0,
            arm_account_api_version="2026-07-01",
            arm_deployment_api_version="2026-07-01",
        )
        missing = SimpleNamespace(status_code=404)
        exists = SimpleNamespace(status_code=200)
        accepted = SimpleNamespace(status_code=202)
        failed = SimpleNamespace(
            status_code=200,
            json=lambda: {"properties": {"provisioningState": "Failed"}},
        )
        ownership = Mock()

        with patch.object(
            adapt,
            "arm_request",
            side_effect=[missing, RuntimeError("PUT failed")],
        ), self.assertRaisesRegex(RuntimeError, "PUT failed"):
            adapt.deploy_for_evaluation(
                SimpleNamespace(),
                config,
                "ft:model",
                "job-1",
                on_accepted=ownership,
            )
        ownership.assert_not_called()

        with patch.object(
            adapt,
            "arm_request",
            return_value=exists,
        ) as request, self.assertRaisesRegex(RuntimeError, "already exists"):
            adapt.deploy_for_evaluation(
                SimpleNamespace(),
                config,
                "ft:model",
                "job-1",
                on_accepted=ownership,
            )
        self.assertEqual(request.call_count, 1)

        captured = {}
        with patch.object(
            adapt,
            "arm_request",
            side_effect=[missing, accepted, failed],
        ), self.assertRaisesRegex(RuntimeError, "provisioning ended"):
            adapt.deploy_for_evaluation(
                SimpleNamespace(),
                config,
                "ft:model",
                "job-1",
                on_accepted=lambda value: captured.update(value),
            )
        self.assertEqual(captured["fine_tuned_model"], "ft:model")
        self.assertEqual(captured["sku"], "DeveloperTier")
        self.assertTrue(captured["deployment_name"].startswith("temporary-eval-"))
        self.assertNotEqual(captured["deployment_name"], config.tuned_deployment)

        matching = SimpleNamespace(
            status_code=200,
            json=lambda: {
                "properties": {"model": {"name": "ft:model"}},
                "sku": {"name": "DeveloperTier"},
            },
        )
        deleted = SimpleNamespace(status_code=204)
        gone = SimpleNamespace(status_code=404)
        client = SimpleNamespace(
            fine_tuning=SimpleNamespace(
                jobs=SimpleNamespace(retrieve=Mock(), cancel=Mock())
            ),
            models=SimpleNamespace(delete=Mock()),
            files=SimpleNamespace(delete=Mock()),
        )
        record = {
            "owned_deployment": captured,
            "fine_tuned_model": "ft:model",
        }
        with patch.object(
            adapt,
            "arm_request",
            side_effect=[matching, deleted, gone],
        ) as request:
            adapt.cleanup(SimpleNamespace(), client, config, record)
        self.assertEqual(request.call_args_list[1].args[1], "DELETE")

        foreign = SimpleNamespace(
            status_code=200,
            json=lambda: {
                "properties": {"model": {"name": "someone-elses-model"}},
                "sku": {"name": "DeveloperTier"},
            },
        )
        foreign_record = {
            "owned_deployment": captured,
            "fine_tuned_model": "ft:model",
        }
        with patch.object(
            adapt,
            "arm_request",
            return_value=foreign,
        ) as request, self.assertRaisesRegex(RuntimeError, "ownership mismatch"):
            adapt.cleanup(SimpleNamespace(), client, config, foreign_record)
        self.assertTrue(
            all(call.args[1] != "DELETE" for call in request.call_args_list)
        )

    def test_current_training_and_deployment_wire_payloads_are_exact(self):
        config = adapt.Config(
            project_endpoint="https://example.services.ai.azure.com/api/projects/p",
            resource_id=(
                "/subscriptions/sub/resourceGroups/rg/providers/"
                "Microsoft.CognitiveServices/accounts/account"
            ),
            region="swedencentral",
            base_model="gpt-4.1-mini",
            base_model_version="2025-04-14",
            base_deployment="gpt-4.1-mini",
            tuned_deployment="temporary",
            training_type="developerTier",
            deployment_sku="DeveloperTier",
            n_epochs=3,
            batch_size=1,
            learning_rate_multiplier=0.1,
            seed=42,
            training_price_per_million_usd=1.0,
            arm_account_api_version="2026-07-01",
            arm_deployment_api_version="2026-07-01",
        )
        config.validate(require_price=True)
        job = adapt.fine_tuning_job_arguments(
            config,
            train_file_id="file-train",
            validation_file_id="file-validation",
        )
        self.assertEqual(job["extra_body"], {"trainingType": "developerTier"})
        self.assertEqual(job["method"]["type"], "supervised")
        deployment = adapt.evaluation_deployment_body(config, "ft:model")
        self.assertEqual(
            deployment["sku"],
            {"name": "DeveloperTier", "capacity": 1},
        )
        self.assertEqual(
            adapt.project_name_from_endpoint(config.project_endpoint),
            "p",
        )


if __name__ == "__main__":
    unittest.main()
