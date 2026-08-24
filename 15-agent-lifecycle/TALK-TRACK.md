# Pattern 15 — Agent lifecycle & promotion (dev → test → prod)

**Group:** Lifecycle, assurance & operations  ·  **Runs 15th of 15** in the run order

**Slide title:** *Promote immutable versions behind one endpoint — roll back without deleting state.*

## In brief
> "The current Foundry model no longer needs a separate Agent Application to obtain an
> endpoint. Creating an agent gives it immutable versions, its own identity and a stable
> `agent_endpoint`. Promotion is a `version_selector` update; rollback pins the prior
> version. The URL and conversation store do not move.
>
> This pipeline creates the same source-controlled definition in dev and test, resolves
> aliases per environment, and runs the Pattern 7 cloud-evaluation gate against the test
> candidate. Only complete passing evidence allows the production version and selector
> update. Missing evidence, a legacy/shared identity, an unresolved alias, a permission
> error, or a selector mismatch fails closed."

## Current, non-legacy publishing surface
- **Use:** the agent object model (`AgentDetails.agent_endpoint`,
  `instance_identity`, immutable versions, `version_selector`).
- **Do not use for new work:** legacy Agent Applications under `/applications`.
- A new agent's stable Responses endpoint is
  `/projects/{project}/agents/{agent}/endpoint/protocols/openai/responses`.
- "Publishing" now means optional distribution to Microsoft 365/Teams, not creating the
  runtime endpoint. This pattern does not automate M365 distribution.

## Source-controlled release contract
[`release-manifest.json`](release-manifest.json) records the canonical name, model alias,
instructions, connection/Toolbox aliases, evaluation dataset and thresholds, release
metadata, and previous approved version. Environment variables resolve topology-specific
project endpoints and aliases; no credentials or endpoints are committed.

## Running it
1. Create dedicated dev/test/prod projects under one Foundry resource for the demo, or
   provide isolated projects/resources/subscriptions in stricter environments.
2. Set `LIFECYCLE_DEV_PROJECT_ENDPOINT`, `LIFECYCLE_TEST_PROJECT_ENDPOINT`,
   `LIFECYCLE_PROD_PROJECT_ENDPOINT`, and `LIFECYCLE_MODEL_DEPLOYMENT`.
3. Bootstrap a prior approved version once:
   `uv run python 15-agent-lifecycle/lifecycle.py bootstrap --approver <operator>`.
4. Release:
   `uv run python 15-agent-lifecycle/lifecycle.py release --approver <operator>`.
   Dev gets an immutable version and smoke test; test gets the candidate pinned and a
   Foundry cloud eval; prod changes only after the gate passes.
5. Demonstrate a block:
   add `--demo-failure`; the eval fails and production remains unchanged.
6. Roll back with the emitted release record:
   `uv run python 15-agent-lifecycle/lifecycle.py rollback --record <record.json> --approver <operator>`.

## Evidence and boundaries
- The pipeline verifies a passing candidate promotion, an explicit failing candidate
  block, unchanged stable endpoint URL, selector rollback, and retrieval of the same
  conversation record after rollback.
- It never deletes the agent, versions, conversation, or state stores during rollback.
- Toolbox has one platform alias: its promoted **default version**. If the manifest marks
  tools changed, the pipeline verifies and updates that default; there are no arbitrary
  named Toolbox aliases in Foundry.
- Foundry has no first-class release-record object. The pipeline writes its own complete
  HMAC-SHA256-signed JSON ledger (protected `LIFECYCLE_RECORD_SIGNING_KEY`) and relies on
  Azure Activity Log/diagnostic settings for
  control-plane audit. The workflow uploads it as a protected 90-day GitHub artifact;
  production deployments should also copy it to an immutable retention-controlled store.
- OIDC is used in [`release.yml`](release.yml); no client secret or credential export.
  CI serializes releases per production endpoint, disables cancellation, and the pipeline
  checks the expected selector immediately before mutation.
- The agent API does not expose a usable ETag/CAS precondition for selector updates.
  Therefore only the serialized pipeline identity should hold agent version/endpoint
  update permission; human and other automation principals must not bypass that lock.
- If any post-promotion smoke, Toolbox, endpoint, or audit-persistence step fails, the
  pipeline compensates by restoring the prior selector and verifies prior behavior.

## Live verification (2026-08-24)
Verified on three isolated projects under one Foundry resource:

- an explicit failure probe was blocked and production stayed on version 1;
- the passing test candidate scored 1.0 classification accuracy and 1.0 schema validity;
- the production stable URL returned `approved` on version 1, the **same URL** returned
  `candidate` after the explicit version-2 selector update, and rollback returned it to
  `approved` on version 1;
- the same stable-endpoint conversation was continued after rollback, with its prior
  items and metadata still present.

The release/evaluation/rollback records are runtime evidence and are not committed because
they contain live resource URLs and identifiers.

## The one-liner
> "Version the definition, gate the candidate, move the selector — never move the endpoint or state."

## Official references
- <https://learn.microsoft.com/azure/foundry/agents/how-to/migrate-agent-applications>
- <https://learn.microsoft.com/azure/foundry/agents/how-to/configure-agent>
- <https://learn.microsoft.com/azure/foundry/agents/how-to/manage-hosted-agent>
- <https://learn.microsoft.com/azure/foundry/agents/quickstarts/set-up-cicd-hosted-agent>
