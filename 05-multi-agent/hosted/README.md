# Pattern 5 (hosted) — Foundry-managed multi-agent orchestration

The same concurrent **Portfolio Analyst + Compliance Officer** workflow used by
[`../orchestrator.py`](../orchestrator.py) is served as a Foundry hosted agent.
`ResponsesHostServer(workflow.as_agent())` exposes the orchestration through the
Responses protocol on port 8088; Foundry supplies managed compute, versioning, logs,
an endpoint, and a dedicated Entra Agent ID.

Pattern 4 remains the default for one loop plus reusable skills. Use this hosted
multi-agent shape when parallel specialists or organizational trust boundaries justify it.

## Layout

```text
hosted/
  azure.yaml
  src/multi-agent-orchestrator/
    workflow.py       # shared by the local and hosted entry points
    main.py           # FoundryChatClient + ResponsesHostServer
    requirements.txt
    Dockerfile
    .env.example
```

## Run and invoke locally

```powershell
cd 05-multi-agent/hosted
copy src\multi-agent-orchestrator\.env.example src\multi-agent-orchestrator\.env
azd ai agent run
```

From a second terminal:

```powershell
azd ai agent invoke --local "Client C-1290 is Conservative, holds 90% equities, and wants a leveraged technology product. Advise."
```

Without the azd extension, run the host directly:

```powershell
uv run --with agent-framework-foundry --with agent-framework-foundry-hosting `
  --with python-dotenv python src/multi-agent-orchestrator/main.py
```

Then invoke the local Responses endpoint:

```powershell
$body = @{
  model = "gpt-5.4-mini"
  input = "Client C-1290 is Conservative, holds 90% equities, and wants a leveraged technology product. Advise."
  stream = $false
} | ConvertTo-Json
Invoke-RestMethod -Uri http://localhost:8088/responses -Method Post `
  -ContentType application/json -Body $body
```

## Deploy and version in Foundry

Install or update the current extensions, authenticate azd to the Foundry tenant, and bind
the manifest to an existing project:

```powershell
azd ext install microsoft.foundry
azd ext upgrade azure.ai.agents
azd auth login --tenant-id <foundry-tenant-id>

$project = "/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<account>/projects/<project>"
azd ai agent init --no-prompt --project-id $project `
  --deploy-mode code --runtime python_3_13 --entry-point main.py `
  --src ./src/multi-agent-orchestrator `
  --agent-name multi-agent-orchestrator-hosted --force

azd deploy
azd ai agent show multi-agent-orchestrator-hosted
azd ai agent invoke multi-agent-orchestrator-hosted `
  "Client C-1290 is Conservative, holds 90% equities, and wants a leveraged technology product. Advise."
```

Each deployment creates a new hosted-agent version. The platform injects
`FOUNDRY_PROJECT_ENDPOINT`; the model deployment is declared in `azure.yaml`. Local auth uses
`DefaultAzureCredential`, and the deployed workload uses its managed identity.
