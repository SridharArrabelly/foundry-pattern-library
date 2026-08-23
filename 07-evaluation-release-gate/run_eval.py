"""
Pattern 7 — Evaluation & release gate (cloud evaluation on Foundry).

Default release-gate mode generates every response from the candidate instructions and
model before uploading the resulting dataset to Foundry's cloud evaluation service. A PR
that changes the candidate can therefore change the scores. The checked-in golden set
contains questions, contexts and expected facts only — never pre-baked candidate answers.

Use --demo-failure to replace one generated answer with an explicit planted regression for
the live failure walkthrough. CI does not enable that mode.

The gate fails closed when the cloud run is not completed, any row errors, any expected
metric is absent, any metric result is malformed, or any required metric reports failures.

Run:
  uv run python 07-evaluation-release-gate/run_eval.py
  uv run python 07-evaluation-release-gate/run_eval.py --demo-failure
"""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from common.foundry import MODEL_DEPLOYMENT_NAME, PROJECT_ENDPOINT

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from openai.types.eval_create_params import DataSourceConfigCustom
from openai.types.evals.create_eval_jsonl_run_data_source_param import (
    CreateEvalJSONLRunDataSourceParam,
    SourceFileID,
)

BASE_DIR = Path(__file__).resolve().parent
DATA = BASE_DIR / "golden_set.jsonl"
CANDIDATE_INSTRUCTIONS = (BASE_DIR / "candidate_instructions.md").read_text(encoding="utf-8")
EXPECTED_CRITERIA = {"groundedness", "relevance", "coherence"}
DEMO_FAILURE_ID = "conservative-overweight"

ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "query": {"type": "string"},
        "context": {"type": "string"},
        "response": {"type": "string"},
        "ground_truth": {"type": "string"},
    },
    "required": ["id", "query", "context", "response", "ground_truth"],
}


def testing_criteria(model: str) -> list[dict]:
    """Return the required Foundry evaluators for this release gate."""
    return [
        {
            "type": "azure_ai_evaluator",
            "name": "groundedness",
            "evaluator_name": "builtin.groundedness",
            "initialization_parameters": {"model": model},
            "data_mapping": {
                "query": "{{item.query}}",
                "context": "{{item.context}}",
                "response": "{{item.response}}",
            },
        },
        {
            "type": "azure_ai_evaluator",
            "name": "relevance",
            "evaluator_name": "builtin.relevance",
            "initialization_parameters": {"model": model},
            "data_mapping": {
                "query": "{{item.query}}",
                "response": "{{item.response}}",
            },
        },
        {
            "type": "azure_ai_evaluator",
            "name": "coherence",
            "evaluator_name": "builtin.coherence",
            "initialization_parameters": {"model": model},
            "data_mapping": {
                "query": "{{item.query}}",
                "response": "{{item.response}}",
            },
        },
    ]


def load_golden_rows(path: Path = DATA) -> list[dict]:
    """Load answer-free fixtures and reject static responses in release-gate input."""
    rows = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            missing = {"id", "query", "context", "ground_truth"} - row.keys()
            if missing:
                raise ValueError(f"{path.name}:{line_number} missing fields: {sorted(missing)}")
            if "response" in row:
                raise ValueError(
                    f"{path.name}:{line_number} contains a static response; "
                    "release-gate responses must be generated from the candidate"
                )
            rows.append(row)
    if not rows:
        raise ValueError(f"{path} contains no evaluation rows")
    return rows


def candidate_input(row: dict) -> str:
    return (
        f"Question:\n{row['query']}\n\n"
        f"Authoritative policy context:\n{row['context']}\n\n"
        "Answer the question from that context."
    )


def generate_candidate_rows(
    openai_client,
    rows: list[dict],
    *,
    model: str,
    demo_failure: bool = False,
) -> list[dict]:
    """Invoke the candidate model/instructions for every golden-set row."""
    generated = []
    for index, row in enumerate(rows, start=1):
        response = openai_client.responses.create(
            model=model,
            instructions=CANDIDATE_INSTRUCTIONS,
            input=candidate_input(row),
        )
        if getattr(response, "status", None) != "completed":
            raise RuntimeError(
                f"candidate response for {row['id']} did not complete: "
                f"{getattr(response, 'status', None)!r}"
            )
        answer = (getattr(response, "output_text", None) or "").strip()
        if not answer:
            raise RuntimeError(f"candidate response for {row['id']} was empty")
        generated.append({**row, "response": answer})
        print(f"  generated {index}/{len(rows)}: {row['id']}")

    if demo_failure:
        target = next((row for row in generated if row["id"] == DEMO_FAILURE_ID), None)
        if target is None:
            raise RuntimeError(f"demo failure fixture {DEMO_FAILURE_ID!r} is missing")
        target["response"] = (
            "Yes. A 90% equity allocation is suitable for a Conservative client and "
            "requires no suitability review."
        )
        print(f"  DEMO ONLY: planted regression in {DEMO_FAILURE_ID}")
    return generated


def write_generated_dataset(rows: list[dict], directory: Path) -> Path:
    path = directory / "candidate_responses.jsonl"
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def gate_failures(run, *, expected_total: int | None = None) -> list[str]:
    """Return every fail-closed reason found in a completed eval run."""
    failures = []
    status = getattr(run, "status", None)
    if status != "completed":
        failures.append(f"run status is {status!r}, expected 'completed'")

    counts = getattr(run, "result_counts", None)
    if counts is None:
        failures.append("result_counts is missing")
    else:
        errored = getattr(counts, "errored", None)
        failed = getattr(counts, "failed", None)
        passed = getattr(counts, "passed", None)
        total = getattr(counts, "total", None)
        if errored is None:
            failures.append("result_counts.errored is missing")
        elif errored > 0:
            failures.append(f"{errored} evaluation row(s) errored")
        if failed is None:
            failures.append("result_counts.failed is missing")
        elif failed > 0:
            failures.append(f"{failed} evaluation row(s) failed")
        if passed is None:
            failures.append("result_counts.passed is missing")
        if total is None or total <= 0:
            failures.append("result_counts.total is missing or zero")
        elif expected_total is not None and total != expected_total:
            failures.append(
                f"result_counts.total is {total}, expected {expected_total} generated row(s)"
            )
        if None not in (passed, failed, errored, total):
            accounted = passed + failed + errored
            if accounted != total:
                failures.append(
                    f"result_counts accounts for {accounted} row(s), expected total {total}"
                )

    criteria_results = getattr(run, "per_testing_criteria_results", None)
    if criteria_results is None:
        failures.append("per_testing_criteria_results is missing")
        return failures

    by_name = {}
    for result in criteria_results:
        name = getattr(result, "testing_criteria", None)
        if not name:
            failures.append("an evaluator result is missing testing_criteria")
            continue
        if name in by_name:
            failures.append(f"duplicate evaluator result: {name}")
            continue
        by_name[name] = result

    missing = EXPECTED_CRITERIA - by_name.keys()
    if "groundedness" in missing:
        failures.append("groundedness result is missing")
    for name in sorted(missing - {"groundedness"}):
        failures.append(f"{name} result is missing")

    for name in sorted(EXPECTED_CRITERIA & by_name.keys()):
        result = by_name[name]
        passed = getattr(result, "passed", None)
        failed = getattr(result, "failed", None)
        errored = getattr(result, "errored", 0)
        if passed is None or failed is None:
            failures.append(f"{name} result is missing passed/failed counts")
            continue
        if errored not in (None, 0):
            failures.append(f"{name} has {errored} errored row(s)")
        if failed > 0:
            failures.append(f"{name} has {failed} failed row(s)")
        if counts is not None and total is not None:
            metric_errored = errored or 0
            accounted = passed + failed + metric_errored
            if accounted != total:
                failures.append(
                    f"{name} accounts for {accounted} row(s), expected total {total}"
                )
    return failures


def print_scorecard(run):
    counts = getattr(run, "result_counts", None)
    print("===== EVAL SCORECARD (Foundry cloud) =====")
    if counts is None:
        print("  result counts: MISSING")
    else:
        print(
            f"  total: {getattr(counts, 'total', 'MISSING')}   "
            f"passed: {getattr(counts, 'passed', 'MISSING')}   "
            f"failed: {getattr(counts, 'failed', 'MISSING')}   "
            f"errored: {getattr(counts, 'errored', 'MISSING')}"
        )
    for result in getattr(run, "per_testing_criteria_results", None) or []:
        name = getattr(result, "testing_criteria", "MISSING")
        passed = getattr(result, "passed", "MISSING")
        failed = getattr(result, "failed", "MISSING")
        print(f"  {name:14s} passed={passed} failed={failed}")
    print("==========================================\n")


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--demo-failure",
        action="store_true",
        help="Plant one wrong generated answer so the live gate failure can be demonstrated.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not PROJECT_ENDPOINT:
        raise SystemExit("Set PROJECT_ENDPOINT in .env")
    if not MODEL_DEPLOYMENT_NAME:
        raise SystemExit("Set MODEL_DEPLOYMENT_NAME in .env")

    credential = DefaultAzureCredential(process_timeout=30)
    credential.get_token("https://ai.azure.com/.default")
    project = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=credential)
    with project:
        openai_client = project.get_openai_client()
        fixtures = load_golden_rows()
        print(f"Generating candidate responses with {MODEL_DEPLOYMENT_NAME} ...")
        generated = generate_candidate_rows(
            openai_client,
            fixtures,
            model=MODEL_DEPLOYMENT_NAME,
            demo_failure=args.demo_failure,
        )

        version = time.strftime("%Y%m%d%H%M%S")
        with tempfile.TemporaryDirectory(prefix="foundry-eval-") as temp:
            dataset_path = write_generated_dataset(generated, Path(temp))
            print("Uploading generated candidate responses to the Foundry project ...")
            data_id = project.datasets.upload_file(
                name="private-banking-candidate-responses",
                version=version,
                file_path=str(dataset_path),
            ).id

        eval_object = openai_client.evals.create(
            name="private-banking-release-gate",
            data_source_config=DataSourceConfigCustom(type="custom", item_schema=ITEM_SCHEMA),
            testing_criteria=testing_criteria(MODEL_DEPLOYMENT_NAME),
        )
        run = openai_client.evals.runs.create(
            eval_id=eval_object.id,
            name=f"candidate-{version}" + ("-demo-failure" if args.demo_failure else ""),
            data_source=CreateEvalJSONLRunDataSourceParam(
                type="jsonl",
                source=SourceFileID(type="file_id", id=data_id),
            ),
        )
        print(f"Foundry eval run: {run.id} (status: {run.status})")

        while run.status in ("queued", "in_progress"):
            time.sleep(5)
            run = openai_client.evals.runs.retrieve(
                run_id=run.id,
                eval_id=eval_object.id,
            )
        print(f"Run {run.status}.\n")
        print_scorecard(run)
        if getattr(run, "report_url", None):
            print(f"Foundry portal -> Evaluations: {run.report_url}\n")

        failures = gate_failures(run, expected_total=len(generated))
        if failures:
            print("GATE FAILED:")
            for failure in failures:
                print(f"  - {failure}")
            return 1
        print("GATE PASSED: candidate output completed and every required metric passed.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
