"""
Pattern 7 — Eval-driven development (CLOUD evaluation on Foundry).

Runs the golden set through Foundry's CLOUD evaluation service so the run shows up in
BOTH places:
  * Terminal — a scorecard (per-evaluator pass/fail) + a CI gate exit code.
  * Foundry portal -> your project -> Evaluations tab — the same run, with per-row
    scores and the judge's reasoning (open the printed report URL).

Row 3 in golden_set.jsonl is deliberately WRONG (says a 90%-equities Conservative
portfolio is suitable) so groundedness FAILS on that row — show the regression being
caught before it ships.

How it works (new Foundry = OpenAI-compatible /evals API):
  1. Upload golden_set.jsonl as a versioned dataset in the project.
  2. evals.create(...)   — define the data schema + evaluators (testing criteria).
  3. evals.runs.create() — run the built-in judges (groundedness/relevance/coherence)
     server-side, keyless (Entra via DefaultAzureCredential).
  4. Poll, print the scorecard, print the portal report URL, gate on groundedness.

Run:  uv run python 07-evaluations/run_eval.py
Exit code is non-zero if any groundedness row fails (this is your CI gate).
"""
import os
import sys
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

DATA = os.path.join(os.path.dirname(__file__), "golden_set.jsonl")

# The golden set rows carry these fields; the evaluators map onto them below.
ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string"},
        "context": {"type": "string"},
        "response": {"type": "string"},
        "ground_truth": {"type": "string"},
    },
    "required": ["query", "response"],
}

# Built-in, agent-grade judges — run server-side in Foundry (no local compute).
#
# The judge model stays on the DIRECT deployment, not the APIM/BYOM route used by
# Patterns 2 and 6 — deliberately. Evaluation is offline QA, not production traffic:
# metering it against the same gateway budget distorts the usage picture, and a large
# eval run could trip the very rate limits that protect live agents. Route what
# represents production; keep test harnesses off that meter. See docs/coexistence.md.
TESTING_CRITERIA = [
    {
        "type": "azure_ai_evaluator",
        "name": "groundedness",
        "evaluator_name": "builtin.groundedness",
        "initialization_parameters": {"model": MODEL_DEPLOYMENT_NAME},
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
        "initialization_parameters": {"model": MODEL_DEPLOYMENT_NAME},
        "data_mapping": {
            "query": "{{item.query}}",
            "response": "{{item.response}}",
        },
    },
    {
        "type": "azure_ai_evaluator",
        "name": "coherence",
        "evaluator_name": "builtin.coherence",
        "initialization_parameters": {"model": MODEL_DEPLOYMENT_NAME},
        "data_mapping": {
            "query": "{{item.query}}",
            "response": "{{item.response}}",
        },
    },
]


def main():
    # One shared credential, pre-warmed, with a longer Azure CLI timeout so the
    # keyless token calls don't flake ('Failed to invoke the Azure CLI').
    credential = DefaultAzureCredential(process_timeout=30)
    credential.get_token("https://ai.azure.com/.default")  # pre-warm the token cache
    project = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=credential)
    with project:
        openai_client = project.get_openai_client()

        # 1) Upload the golden set as a versioned dataset (a new version each run).
        version = time.strftime("%Y%m%d%H%M%S")
        print("Uploading golden set to the Foundry project ...")
        data_id = project.datasets.upload_file(
            name="private-banking-golden-set", version=version, file_path=DATA
        ).id

        # 2) Define the evaluation (schema + evaluators).
        eval_object = openai_client.evals.create(
            name="private-banking-suitability",
            data_source_config=DataSourceConfigCustom(type="custom", item_schema=ITEM_SCHEMA),
            testing_criteria=TESTING_CRITERIA,
        )

        # 3) Run it against the uploaded dataset (judges run server-side in Foundry).
        run = openai_client.evals.runs.create(
            eval_id=eval_object.id,
            name=f"golden-set-{version}",
            data_source=CreateEvalJSONLRunDataSourceParam(
                type="jsonl", source=SourceFileID(type="file_id", id=data_id)
            ),
        )
        print(f"Foundry eval run: {run.id}  (status: {run.status})")

        # 4) Poll until the cloud run finishes.
        while run.status in ("queued", "in_progress"):
            time.sleep(5)
            run = openai_client.evals.runs.retrieve(run_id=run.id, eval_id=eval_object.id)
        print(f"Run {run.status}.\n")

        # 5) Scorecard — per-evaluator pass/fail across the golden set.
        rc = run.result_counts
        print("===== EVAL SCORECARD (Foundry cloud) =====")
        print(f"  rows: {rc.total}   passed: {rc.passed}   failed: {rc.failed}   errored: {rc.errored}")
        failed_ground = 0
        for c in run.per_testing_criteria_results:
            flag = "OK" if c.failed == 0 else "FAIL"
            print(f"  {c.testing_criteria:14s} passed={c.passed} failed={c.failed}  [{flag}]")
            if c.testing_criteria == "groundedness":
                failed_ground = c.failed
        print("==========================================\n")

        print(f"Foundry portal -> Evaluations (open this): {run.report_url}\n")

        # 6) CI gate — any grounded-ness failure blocks the merge (the planted wrong row).
        if failed_ground > 0:
            print(f"GATE FAILED: {failed_ground} row(s) failed groundedness — block the merge.")
            sys.exit(1)
        print("GATE PASSED: every row is grounded.")


if __name__ == "__main__":
    main()
