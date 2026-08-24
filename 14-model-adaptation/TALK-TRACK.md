# Pattern 14 — Model adaptation (fine-tuning & evaluation)

**Group:** Agent construction & knowledge  ·  **Runs 7th of 15** in the run order

**Slide title:** *Adapt stable behavior — then prove the gain on untouched data.*

## In brief
> "Fine-tuning is not a knowledge refresh mechanism. Use **RAG, Search or Foundry IQ**
> for changing facts, and prompt engineering for lightweight instructions. Fine-tune
> when the target is stable task behavior: strict output format, consistent
> classification, tool-selection patterns, or examples too numerous for the prompt.
>
> This industry-neutral triage task has four fixed, opaque routing codes (`AX7`, `BR2`,
> `CZ9`, `UNSUPPORTED`) and an exact JSON schema. The base prompt exposes the allowed
> codes and schema but not a contrived verbal mapping; the reviewed examples are the
> stable behavior specification the adapted model must learn.
> We first evaluate the base deployment on a separate held-out set. Only then do we
> upload reviewed training and validation JSONL, submit a current Foundry supervised
> fine-tuning job, deploy the result on the evaluation-only Developer tier, and run the
> identical held-out evaluation. Schema validity, accuracy, adherence, tokens and latency
> decide the gate — never training loss alone."

## What is implemented
- Reviewed `train.jsonl` and `validation.jsonl`, plus a separately hashed held-out
  `test.jsonl`; validators reject duplicates, leakage, malformed examples and common PII.
- Base and tuned evaluation through the same Responses API call and strict JSON schema.
- Current Foundry job path:
  `AIProjectClient.get_openai_client()` → Files → `fine_tuning.jobs.create` with
  supervised method and explicit `trainingType`.
- ARM control-plane deployment of the resulting model as `DeveloperTier` (evaluation
  only, 24-hour lifetime, no SLA or data-residency guarantee).
- A fail-closed release gate and one reproducibility record containing model/version,
  dataset hashes, hyperparameters, job/evaluation IDs, duration, configured price
  snapshot, estimated training cost, metrics and cleanup evidence.
- Optional synthetic candidates only through
  `--enable-preview-synthetic`; the feature is **Preview**, outputs are marked unreviewed,
  deduplicated, excluded from held-out data and never consumed automatically.

## Running it
1. Configure the Sweden Central or North Central US Foundry project/resource values in
   `.env`. The fine-tuning data-plane currently requires the Foundry resource's
   **default project**. `gpt-4.1-mini` version `2025-04-14` is the default GA SFT model.
2. Validate datasets without cloud access:
   `uv run python 14-model-adaptation/adapt_model.py preflight --offline`.
3. Run live preflight:
   `uv run python 14-model-adaptation/adapt_model.py preflight`.
   It verifies the deployment model/version, resource region, fine-tuning job access,
   effective deployment-write **and delete** permissions and the regional usage endpoint.
4. Capture the current training rate from the Azure pricing page in
   `FINETUNE_TRAINING_PRICE_PER_MILLION_USD`. The repository never hardcodes a price.
5. Run the complete billable lifecycle only after review:
   `uv run python 14-model-adaptation/adapt_model.py run --confirm-cost`.
   The default training type is **Developer** (wire value `developerTier`,
   cheap/preemptible training capacity); the temporary inference SKU is separately
   named **Developer** in product language (ARM `2026-07-01` wire value
   `DeveloperTier`). Older API examples may use different casing; the payload test pins
   the API version and live-accepted value together.
6. Inspect the single record under `14-model-adaptation/artifacts/`. The temporary
   deployment and uploaded train/validation files are deleted even when the gate fails.
   If training succeeded but a later step failed, resume without retraining:
   `uv run python 14-model-adaptation/adapt_model.py resume --submission-record <submission.json> --baseline <base.json> --output <record.json> --confirm-evaluation-cost`.
   Resume verifies the job, files, model/version, training type, hyperparameters, dataset
   hashes and evaluation-protocol hash against that immutable submission record.

## Release rule
No promotion when the tuned result misses an absolute threshold, lacks the configured
gain, regresses any category, exceeds token/latency tolerances, has incomplete usage
evidence, or leaves cleanup incomplete. A perfect or near-perfect base result can
therefore block fine-tuning as unnecessary — that is a successful cost decision.

## Boundaries
- Fine-tuning permissions and deployment permissions are separate. Foundry User can
  access training in supported configurations; deployment requires Foundry Owner or
  both `Microsoft.CognitiveServices/accounts/deployments/write` and `/delete`.
- Fine-tuning through a non-default project endpoint fails with a service `forbidden`
  response. Preflight resolves the project ARM resource and blocks before uploading data.
- Standard `gpt-4.1-mini` SFT is region-limited. Global/Developer training can relax
  locality but has no data-residency guarantee; choose deliberately.
- The general regional usage API is an inferred quota preflight; job submission remains
  the definitive capacity check.
- Exact price is supplied at runtime because pricing changes independently of this repo.
- No customer facts, changing business knowledge or PII belong in these datasets.
- The baseline and evaluation-protocol hash are persisted before upload. Resume rejects
  any job, files, dataset, model/version, training type, hyperparameters or baseline that
  does not match the immutable submission record.
- If a failure leaves a job nonterminal, cleanup cancels it, waits for terminal state,
  then removes uploaded files so a retry cannot accidentally duplicate paid training.

## Live verification (2026-08-24)
- `gpt-4.1-mini` SFT job succeeded on reviewed data: 8,073 trained tokens, about
  **$0.040365** at the captured $5/M GlobalStandard training rate, in about 3,912 seconds.
- Base held-out result: 0.50 classification accuracy, 1.0 schema validity/adherence,
  382 output tokens, 2,050 ms mean latency.
- Tuned held-out result: **0.50 accuracy** (no gain), 1.0 schema/adherence,
  353 output tokens, 2,414 ms mean latency.
- The gate correctly **rejected promotion**: no accuracy gain, tuned accuracy below the
  minimum, and AX7 category accuracy regressed. We did not tune again against the now
  observed held-out set.
- The temporary DeveloperTier deployment and uploaded files were deleted. The project
  Models API returned 404 for model deletion; the completed job/checkpoint remains as
  service-side reproducibility metadata with no hosting charge after deployment removal.
- The first pre-hardening run had deleted its sole baseline artifact. The new
  provenance-bound resume path correctly rejected a different baseline evaluation ID
  instead of attaching it to that job. We retained the separately captured identical-set
  base/tuned comparison above and did not retrain or tune against the observed test set.

## The one-liner
> "Fine-tune stable behavior, not changing knowledge — and ship only a measured gain."

## Official references
- <https://learn.microsoft.com/azure/foundry/openai/how-to/fine-tuning>
- <https://learn.microsoft.com/azure/foundry/openai/how-to/fine-tuning-deploy>
- <https://learn.microsoft.com/azure/foundry/foundry-models/concepts/deployment-types>
- <https://learn.microsoft.com/azure/foundry/fine-tuning/data-generation>
