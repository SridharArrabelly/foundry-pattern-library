# Pattern 9 — Cross-cloud protocol gateway (APIM + MCP + A2A)

Pattern 9 proves two different cross-cloud interaction models through one governed Azure API
Management boundary:

1. A real Foundry prompt agent invokes a selected REST operation as an MCP tool through a
   real APIM REST-backed MCP API.
2. A Foundry-side A2A client sends JSON-RPC to a real APIM A2A API, which fronts a genuine
   agent card and minimal A2A task runtime.

The backend is intentionally labeled **AWS Lambda / Amazon Bedrock (simulated)** because this
catalog has no AWS environment. APIM, MCP, A2A, deterministic correlation, and the optional
Foundry prompt-agent call are real when deployed.

## Two distinct lanes

```text
MCP — invoke a capability as a tool
Foundry prompt agent -> APIM MCP API -> selected APIM REST operation -> simulated Lambda tool

A2A — communicate with an independently operating agent
Foundry-side client -> APIM A2A API -> A2A JSON-RPC + agent card -> simulated Bedrock agent
```

The A2A adapter is not a relabeled Bedrock API. `simulator.py` publishes an agent card,
implements `message/send` and `tasks/get`, returns A2A task/artifact envelopes, and uses
deterministic message/task/result correlation. Unsupported transports and methods fail with
explicit protocol errors.

## Local protocol checks

Run the backend locally:

```powershell
uv run uvicorn simulator:app --app-dir 09-aws-interop --port 8000
```

Then inspect:

```text
GET  http://localhost:8000/health
GET  http://localhost:8000/v1/capabilities
POST http://localhost:8000/v1/quotes
GET  http://localhost:8000/.well-known/agent-card.json
POST http://localhost:8000/a2a
```

Run the deterministic, malformed-envelope, wrong-ID, unsupported-method/transport,
agent-card mismatch, and secret-leakage tests:

```powershell
uv run python -m unittest tests.test_protocol_gateway -v
```

## Deploy APIM and the scale-to-zero backend

Follow [`infra/README.md`](infra/README.md). The deployment creates a dedicated resource
group for Container Apps and ACR, then creates only marker-owned REST, MCP, and A2A APIs
inside the existing APIM service. It refuses to continue if a resource name is already owned
by something else or if APIM global diagnostics log MCP frontend response payload bytes.
The preview management contract is explicit: the tool surface is `type: "mcp"` and the
independent-agent surface is `type: "a2a"`. A pattern-owned secret named value authenticates
APIM to the external Container App, so direct calls cannot bypass gateway enforcement.

`deploy.ps1` writes the APIM key and live URLs to a caller-selected file outside Git. Load
those values into the current process and run:

```powershell
uv run python 09-aws-interop/verify_live.py --evidence <outside-git-path>
```

The verifier calls the live Container App through all three APIM surfaces and checks:

- deterministic equality across REST, MCP, and A2A;
- MCP tool discovery and `create_quote` invocation;
- APIM-rewritten A2A agent-card transport plus JSON-RPC task/result output;
- malformed JSON, unknown task ID, unsupported method, and unsupported transport handling;
- rejection of direct backend capability, quote, agent-card, and A2A calls;
- explicit simulated-AWS/Bedrock labels and absence of the APIM key from evidence.

## Run the Foundry MCP lane

Create a marker-owned Foundry project custom-key connection targeting `PATTERN9_MCP_URL`.
The script reads the APIM subscription key from the current process and stores it under the
`Ocp-Apim-Subscription-Key` header; it does not print the key or accept it as a command-line
argument:

```powershell
$projectId = "<complete-foundry-project-resource-id>"
.\09-aws-interop\infra\foundry_connection.ps1 `
  -Action create `
  -ProjectResourceId $projectId
$env:PATTERN9_FOUNDRY_CONNECTION_NAME = "<connection-name>"
uv run python 09-aws-interop/foundry_mcp_agent.py
.\09-aws-interop\infra\foundry_connection.ps1 `
  -Action delete `
  -ProjectResourceId $projectId
```

The script creates a versioned prompt agent with `MCPTool`, allow-lists only `create_quote`,
uses `require_approval="never"` because the operation is read-only, invokes the agent through
the Responses API, and requires a completed real `mcp_call` output item containing deterministic
correlation and the simulation label. Equivalent model retries are accepted and counted; retries
with invalid result payloads are counted separately, and valid retries that disagree fail
verification. Because model-generated tool arguments can vary, the verifier allows up to three
Responses attempts but does not retry transport or authentication failures. The temporary agent
is deleted by default.
Only the version created by the verifier is deleted, so a caller-supplied agent name cannot
remove other versions.
The connection script also refuses to replace or delete a connection without the Pattern 9
ownership marker.

## Optional future real-AWS backend

`mcp_aws_lambda_server.py` is an explicitly unverified adapter for a future real Lambda.
Install it only with `uv sync --extra aws`, configure credentials outside the repository, and
perform an environment-specific security review. It is not evidence for this pattern.
