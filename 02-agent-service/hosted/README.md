# Pattern 2 (hosted) — a real Foundry **hosted agent**

Your own **Agent Framework** code, containerized and run by **Foundry Agent Service**.
The platform pulls the image, provisions compute, gives the agent its **own Entra Agent ID**,
and exposes a dedicated endpoint. This is the "bring-your-own-code" hosting model — distinct
from the prompt-based agent in [`../create_prompt_agent.py`](../create_prompt_agent.py).

```
hosted/
  azure.yaml                 # azd manifest: kind: hosted, Responses protocol
  src/rm-assistant/
    main.py                  # FoundryChatClient + @tool functions + ResponsesHostServer
    policy.md                # suitability policy (grounding, shipped in the image)
    requirements.txt         # agent-framework-foundry, agent-framework-foundry-hosting
    Dockerfile               # serves on :8088
    .env.example             # local-only vars (injected automatically in Foundry)
```

## Prerequisites (one-time)

```powershell
winget upgrade Microsoft.Azd          # need azd >= 1.27 (older azd = "extension incompatible")
azd ext install microsoft.foundry     # provides the `azd ai agent` commands (+ deps)
azd ext upgrade azure.ai.agents       # must be >= 1.0.0-beta.4 (see requiredVersions in azure.yaml)

# Sign azd in to the tenant that OWNS the Foundry resource (not whatever you last used):
azd auth login --tenant-id 00000000-0000-0000-0000-000000000000
```

You also need the **Foundry Project Manager** role on the `your-project` project to deploy.

> **Tenant gotcha (this bit us):** `azd auth` is separate from `az login`. If azd is signed
> into a different tenant, `azd deploy` targets the wrong directory and fails. Always pass
> `--tenant-id` for the Foundry resource's tenant.

## Run it locally (safe demo — no cloud deploy)

> ⚠️ **`main.py` starts a *server* and then blocks — that's correct, not a hang.** You'll see
> `Running on http://0.0.0.0:8088` and the process just sits there waiting for a request.
> Nothing happens until you POST a question from a **second terminal** (below). Leaving it
> running for 30 minutes with no request is expected; it simply idles.

**Terminal 1 — start the host:**

```powershell
cd 02-agent-service/hosted
copy src\rm-assistant\.env.example src\rm-assistant\.env
azd ai agent run                                   # starts the host on http://localhost:8088
```

**Terminal 2 — send it a question:**

```powershell
azd ai agent invoke --local "What is the suitability rule, and is client C-1290 compliant?"
```

> **No azd extension yet?** Run the container code directly in Terminal 1:
> `uv run --with agent-framework-foundry --with agent-framework-foundry-hosting --with python-dotenv python src/rm-assistant/main.py`
> — then, in Terminal 2, POST to the Responses endpoint:
>
> ```powershell
> $body = @{ model = "gpt-5.4-mini"; input = "Is client C-1290 compliant per the suitability policy?"; stream = $false } | ConvertTo-Json
> Invoke-RestMethod -Uri http://localhost:8088/responses -Method Post -ContentType application/json -Body $body |
>   ForEach-Object { ($_.output | Where-Object type -eq message).content.text }
> ```
>
> **The red `169.254.169.254` traceback at startup is benign.** It's the OpenTelemetry Azure
> resource-detector probing the instance-metadata endpoint (IMDS) to see if it's running on
> Azure hardware. On a laptop that fails fast (~15 ms) and repeats in each metrics dump — it
> does **not** affect the agent. Inside the Foundry container the probe succeeds and goes quiet.
>
> **Local latency tip:** the sample `.env` sets `AZURE_TOKEN_CREDENTIALS=dev` so
> `DefaultAzureCredential` uses your `az login` and skips the managed-identity token probe
> (which only resolves inside the Foundry container and otherwise adds ~80 s to the first call).
> It also pins `AZURE_TENANT_ID` to the resource tenant. Both are local-only — don't set them in Foundry.

## Deploy to Foundry (real hosted agent)

Bind to the **existing** `your-project` project (so azd deploys the agent there instead of
provisioning new infra), then deploy. This uses **code deploy** — a ZIP of `src/rm-assistant/`
is built remotely, so **no local Docker is needed**.

```powershell
cd 02-agent-service/hosted

# One-time: bind the azd env to the existing project by resource id (code deploy, Python 3.13)
$proj = "/subscriptions/<sub>/resourceGroups/your-resource-group/providers/Microsoft.CognitiveServices/accounts/your-foundry-resource/projects/your-project"
azd ai agent init --no-prompt --project-id $proj `
  --deploy-mode code --runtime python_3_13 --entry-point main.py `
  --src ./src/rm-assistant --agent-name rm-assistant-hosted --force

azd deploy                              # remote build → creates agent version 1 + Entra Agent ID (~2-3 min)
azd ai agent show rm-assistant-hosted   # ID, version, Status=active, endpoint, agent identity
azd ai agent invoke rm-assistant-hosted "Is client C-1290 compliant per the suitability policy?"
azd ai agent monitor rm-assistant-hosted   # live container logs
```

> **Runtime note:** local dev uses Python 3.12 (the `Dockerfile`); code-deploy uses the
> platform runtime (`python_3_13`). They're independent — the same `main.py` runs on both.
> `main.py` reads `FOUNDRY_PROJECT_ENDPOINT` (auto-injected by the platform) and the model
> name from the `env:` map in `azure.yaml` (with a `gpt-5.4-mini` fallback), so the container
> never boots blind.

Each `azd deploy` creates a **new version** (v2, v3…); the latest is active by default.

**Portal:** the deploy prints a **Playground URL** — or go to Foundry → `your-project` →
**Agents** → `rm-assistant-hosted`. There you can **chat with it**, see its **Identity**
(dedicated Entra Agent ID) and **Logs**. Compute deprovisions after ~15 min idle → no idle cost.

## Cleanup

```powershell
azd down               # remove the hosted agent (and azd-provisioned resources)
```

## The two hosting models — say this out loud

| | Prompt-based agent (`../create_prompt_agent.py`) | Hosted agent (this folder) |
|---|---|---|
| You provide | model + instructions + tools (config) | your **code/container** (any framework) |
| Runtime | Foundry-managed assistant | your process on managed compute |
| State | managed threads + vector store | you own it (or use Foundry memory/toolbox) |
| Identity | Entra Agent ID | **dedicated** Entra Agent ID |
| Best when | fast, declarative, File Search RAG | custom orchestration, custom protocols, control |

Both are governable Foundry agents with an Entra Agent ID — the point of Pattern 2: **you
didn't build a runtime, an identity system and a hosting plane. Foundry did.**
